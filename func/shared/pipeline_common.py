"""Step01~Step10 파이프라인의 공용 파일시스템·상태·안전 진단 보조 함수입니다."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from func.shared.runtime_paths import logical_ref, resolve_logical_ref, runtime_dir, runtime_path, sanitize_physical_paths


PIPELINE_STEP_IDS = tuple(f"step{index:02d}" for index in range(1, 11))
OUTPUT_MODES = ("operational", "debug")
DEPRECATED_OUTPUT_MODE_ALIASES = {"audit": "debug"}
WINDOWS_PATH = re.compile(r"(?:[A-Za-z]:\\|\\\\[^\\\s]+\\[^\\\s]+\\)[^\s\"']*")
WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_init(init: str) -> None:
    if not re.fullmatch(r"\d{10}", init):
        raise ValueError("init은 YYYYMMDDHH 형식이어야 합니다.")
    if init[8:] not in {"00", "06", "12", "18"}:
        raise ValueError("init 시각은 00, 06, 12, 18 중 하나여야 합니다.")


def rel(root: Path, path: Path) -> str:
    return logical_ref(root, path)


def normalize_source_reference_paths(root: Path, value: Any) -> Any:
    """Replace host-specific file references with stable project-relative paths."""
    source_path_keys = {"source_field_paths", "source_files"}
    resource_path_keys = {
        "coastline_source_path",
        "borders_source_path",
        "shape_source_paths",
        "coastline_source_paths",
        "border_source_paths",
        "admin1_source_paths",
    }
    project_anchors = ("dain", "daio", "daba", "prompts", "scripts")

    def normalize_one_path(item: str) -> str:
        normalized_text = item.replace("\\", "/")
        tokens = [token for token in normalized_text.split("/") if token]
        anchor_positions = [
            (index, token)
            for index, token in enumerate(tokens)
            if token in project_anchors
        ]
        # Temporary workspaces contain a copied daba tree.  Strip all host and
        # tmp/run prefixes and retain only the stable project resource path.
        if anchor_positions:
            index, _anchor = anchor_positions[-1]
            return Path(*tokens[index:]).as_posix()

        path = Path(item)
        if not path.is_absolute() and not WINDOWS_DRIVE_PATH.match(normalized_text):
            return path.as_posix()
        try:
            return logical_ref(root, resolve_logical_ref(root, item))
        except (ValueError, OSError):
            raise ValueError(f"프로젝트 외부 경로를 정규화할 수 없습니다: {item}") from None

    def normalize_path_list(items: list[Any]) -> list[Any]:
        normalized: list[Any] = []
        for item in items:
            if not isinstance(item, str):
                normalized.append(item)
                continue
            parts = [part.strip() for part in item.split(";") if part.strip()]
            normalized.append(";".join(normalize_one_path(part) for part in parts))
        return normalized

    if isinstance(value, list):
        return [normalize_source_reference_paths(root, item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {key: normalize_source_reference_paths(root, item) for key, item in value.items()}
    for key in source_path_keys | resource_path_keys:
        raw = value.get(key)
        if isinstance(raw, list):
            normalized[key] = normalize_path_list(raw)
        elif isinstance(raw, str):
            normalized[key] = normalize_one_path(raw)
    catalog = value.get("source_file_catalog")
    if isinstance(catalog, dict):
        normalized["source_file_catalog"] = {
            str(key): normalize_one_path(item) if isinstance(item, str) else item
            for key, item in catalog.items()
        }
    return normalized


def resolve_output_mode(value: str | None = None) -> str:
    """Resolve CLI > environment > operational output mode.

    ``audit`` remains a deprecated compatibility alias and is mapped to
    ``debug`` without creating audit directories in an operational path.
    """
    raw = str(value or os.getenv("AIFS_OUTPUT_MODE", "operational")).strip().lower()
    raw = DEPRECATED_OUTPUT_MODE_ALIASES.get(raw, raw)
    if raw not in OUTPUT_MODES:
        raise ValueError(
            f"output mode는 {', '.join(OUTPUT_MODES)} 중 하나여야 합니다: {raw!r}"
        )
    return raw


def add_output_mode_argument(parser: Any) -> None:
    parser.add_argument(
        "--output-mode",
        choices=[*OUTPUT_MODES, *DEPRECATED_OUTPUT_MODE_ALIASES],
        default=None,
        help=(
            "산출물 모드. 기본 operational; debug는 tmp/logs에만 상세자료를 보존합니다. "
            "audit은 deprecated alias이며 debug로 매핑됩니다."
        ),
    )


@contextmanager
def step_workspace(*, root: Path, init: str, step: str, output_mode: str) -> Iterator[Path]:
    """Create a step-scoped temporary workspace under tmp/<init>/<step>.

    Operational mode removes the workspace on both success and failure. Debug
    mode preserves it for explicit troubleshooting, never under daio.
    """
    mode = resolve_output_mode(output_mode)
    base = runtime_path(root, "tmp", init, step)
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)
    # On Windows, tempfile.mkdtemp() may create a workspace whose inherited
    # ACL rejects creation of child directories.  Use an explicit project
    # temporary path so the step engine can create its working tree normally.
    workspace = base / "run_workspace"
    workspace.mkdir(parents=False, exist_ok=False)
    try:
        yield workspace
    finally:
        if mode == "operational":
            shutil.rmtree(base, ignore_errors=True)


def sanitize_operational_log_text(value: object, *, root: Path) -> str:
    """Remove host-specific paths and credentials from operational logs."""
    text = sanitize_physical_paths(str(value), root=root, replacement="<PROJECT_ROOT>")
    try:
        text = text.replace(str(Path.home().resolve()), "<USER_HOME>")
    except OSError:
        pass
    text = re.sub(r"(?i)[A-Z]:\\[^\s\"']*(?:\\dain\\|\\raw\\|\.grib\d?\b)[^\s\"']*", "<DATA_ROOT>", text)
    text = re.sub(r"(?i)[A-Z]:\\[^\s\"']*(?:\\python(?:\.exe)?|\\lib\\)[^\s\"']*", "<PYTHON_HOME>", text)
    text = WINDOWS_PATH.sub("<LOCAL_PATH>", text)
    text = re.sub(r"(?i)\b(?:nvidia_api_key|openai_api_key|api[_-]?key)\b(?:\s*[:=]\s*(?:bearer\s+)?[^\s,;]+)?", "<REDACTED>", text)
    text = re.sub(r"(?i)\bauthorization\b(?:\s*[:=]?\s*(?:bearer\s+)?[^\s,;]+)?", "<REDACTED>", text)
    return re.sub(r"(?i)\bbearer\b(?:\s+[^\s,;]+)?", "<REDACTED>", text)


class _SanitizingFormatter(logging.Formatter):
    def __init__(self, *args: Any, root: Path, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._root = root

    def format(self, record: logging.LogRecord) -> str:
        record.msg = sanitize_operational_log_text(record.getMessage(), root=self._root)
        record.args = ()
        return super().format(record)

    def formatException(self, ei: Any) -> str:  # noqa: N802
        return sanitize_operational_log_text(super().formatException(ei), root=self._root)


def sha256sum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 객체가 필요합니다: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def step_dir(root: Path, init: str, step: str) -> Path:
    return runtime_path(root, "daio", init, step)


def setup_pipeline_logging(root: Path, init: str, step: str = "pipeline") -> logging.Logger:
    log_path = runtime_path(root, "logs", init, f"{step}.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"aifsheavyrain.{init}.{step}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == log_path for handler in logger.handlers):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        formatter = _SanitizingFormatter("%(asctime)s UTC %(levelname)s %(message)s", root=root)
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def _default_status(init: str) -> dict[str, Any]:
    return {
        "init": init,
        "pipeline_status": "not_started",
        "updated_at_utc": now_utc(),
        "steps": {step: {"status": "not_started", "summary": {}} for step in PIPELINE_STEP_IDS},
    }


def _load_pipeline_status(path: Path, init: str) -> dict[str, Any]:
    """Normalize legacy status files to the active Step01-Step10 pipeline contract."""
    current = load_json(path) if path.is_file() else _default_status(init)
    raw_steps = current.get("steps")
    raw_steps = raw_steps if isinstance(raw_steps, dict) else {}
    current["init"] = init
    current["steps"] = {
        step: raw_steps.get(step)
        if isinstance(raw_steps.get(step), dict)
        else {"status": "not_started", "summary": {}}
        for step in PIPELINE_STEP_IDS
    }
    return current



def _derive_pipeline_status(step_states: list[str]) -> str:
    """Derive the run state while treating reused inputs as completed work."""
    if "failed" in step_states:
        return "failed"
    if "running" in step_states:
        return "running"
    if step_states and all(state in {"completed", "reused"} for state in step_states):
        return "completed"
    if any(state in {"completed", "reused"} for state in step_states):
        return "partial_completed"
    return "not_started"

def update_run_status(
    *, root: Path, init: str, step: str, status: str, summary: dict[str, Any] | None = None
) -> None:
    if step not in PIPELINE_STEP_IDS:
        raise ValueError(f"알 수 없는 파이프라인 단계입니다: {step}")
    path = runtime_path(root, "daio", init, "run_status.json")
    current = _load_pipeline_status(path, init)
    if status == "running":
        # A rerun of an upstream step invalidates every downstream status from
        # the previous run.  Keep existing files for reuse/audit, but never let
        # run_status.json present stale Step09/10 completion after Step08 fails.
        step_index = PIPELINE_STEP_IDS.index(step)
        for downstream_step in PIPELINE_STEP_IDS[step_index + 1:]:
            current["steps"][downstream_step] = {"status": "not_started", "summary": {}}
    current["steps"][step] = {"status": status, "summary": summary or {}}
    states = [str(item.get("status")) for item in current["steps"].values()]
    current["pipeline_status"] = _derive_pipeline_status(states)
    current["updated_at_utc"] = now_utc()
    write_json(path, current)
    if status == "completed":
        # run_status.json is the active-run source of truth.  Preserve a prior
        # failure record for audit, but remove it from the active step directory
        # so a later successful run is never represented as failed.
        active_failure = step_dir(root, init, step) / "failure_summary.json"
        if active_failure.is_file():
            history_dir = active_failure.parent / "failure_history"
            history_dir.mkdir(parents=True, exist_ok=True)
            timestamp = now_utc().replace(":", "").replace("+00:00", "Z")
            history_path = history_dir / f"failure_summary_{timestamp}.json"
            shutil.copy2(active_failure, history_path)
            active_failure.unlink()


def update_selected_validation_status(
    *,
    root: Path,
    init: str,
    step: str,
    status: str,
    summary: dict[str, Any],
) -> None:
    """Record a partial validation without changing the full pipeline state."""
    if step not in PIPELINE_STEP_IDS:
        raise ValueError(f"알 수 없는 파이프라인 단계입니다: {step}")
    if status not in {"selected_validation_completed", "selected_validation_failed"}:
        raise ValueError(f"선택 검증 상태가 올바르지 않습니다: {status}")
    path = runtime_path(root, "daio", init, "run_status.json")
    current = _load_pipeline_status(path, init)
    step_state = current["steps"].setdefault(step, {"status": "not_started", "summary": {}})
    existing_summary = step_state.get("summary")
    step_summary = dict(existing_summary) if isinstance(existing_summary, dict) else {}
    step_summary["last_selected_validation"] = {"status": status, **summary}
    step_state["summary"] = step_summary
    current["steps"][step] = step_state
    states = [str(item.get("status")) for item in current["steps"].values()]
    current["pipeline_status"] = _derive_pipeline_status(states)
    current["updated_at_utc"] = now_utc()
    write_json(path, current)


def sanitize_failure_text(value: object, *, root: Path) -> str:
    text = sanitize_physical_paths(str(value), root=root, replacement="<project-root>")
    text = re.sub(
        r"(?i)\b(?:nvidia_api_key|openai_api_key|api[_-]?key)\b(?:\s*[:=]\s*(?:bearer\s+)?[^\s,;]+)?",
        "<redacted>",
        text,
    )
    text = re.sub(
        r"(?i)\bauthorization\b(?:\s*[:=]?\s*(?:bearer\s+)?[^\s,;]+)?",
        "<redacted>",
        text,
    )
    text = re.sub(r"(?i)\bbearer\b(?:\s+[^\s,;]+)?", "<redacted>", text)
    return WINDOWS_PATH.sub("<local-path>", text)[:2000]


def write_failure_summary(
    *,
    root: Path,
    init: str,
    step: str,
    exc: Exception,
    failed_candidate_id: str | None = None,
    failed_request_id: str | None = None,
    diagnostics: dict[str, Any] | None = None,
    run_id: str | None = None,
    started_at_utc: str | None = None,
    output_dir: Path | None = None,
) -> Path:
    path = (output_dir or step_dir(root, init, step)) / "failure_summary.json"
    allowed_diagnostic_fields = {
        "provider", "api_family", "stage", "candidate_id", "model", "status",
        "incomplete_reason", "configured_max_output_tokens", "input_tokens",
        "cached_input_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens",
        "total_tokens", "visible_output_token_estimate", "partial_output_present",
        "partial_output_utf8_bytes", "partial_output_characters",
        "partial_output_sha256_prefix", "output_item_count", "output_item_types",
        "response_id", "request_id", "retry_count", "elapsed_seconds", "prompt_sha256",
        "transport_schema_sha256", "canonical_schema_sha256", "input_sha256",
        "incomplete_diagnosis",
        "semantic_unit_repair", "repair_attempt_count", "repair_attempts_by_check",
        "candidate_chunk_repair_attempt_count", "candidate_chunk_repair_history",
        "final_validation_errors",
    }
    safe_diagnostics = (
        {
            key: value
            for key, value in diagnostics.items()
            if key in allowed_diagnostic_fields
        }
        if isinstance(diagnostics, dict)
        else None
    )
    if isinstance(safe_diagnostics, dict):
        raw_errors = safe_diagnostics.get("final_validation_errors")
        if isinstance(raw_errors, list):
            safe_diagnostics["final_validation_errors"] = [
                sanitize_failure_text(item, root=root)
                for item in raw_errors[:12]
            ]
        raw_history = safe_diagnostics.get("candidate_chunk_repair_history")
        if isinstance(raw_history, list):
            history_keys = (
                "candidate_id", "stage", "repair_unit", "repair_attempt",
                "repair_attempt_limit", "validation_errors", "next_validation_errors", "result",
            )
            bounded_history: list[dict[str, Any]] = []
            for row in raw_history[-3:]:
                if not isinstance(row, dict):
                    continue
                item = {key: row[key] for key in history_keys if key in row}
                for field in ("validation_errors", "next_validation_errors"):
                    values = item.get(field)
                    if isinstance(values, list):
                        item[field] = [
                            sanitize_failure_text(value, root=root)
                            for value in values[:12]
                        ]
                bounded_history.append(item)
            safe_diagnostics["candidate_chunk_repair_history"] = bounded_history
    summary = {
        "failed_step": step,
        "failed_candidate_id": failed_candidate_id,
        "failed_request_id": failed_request_id,
        "error_type": type(exc).__name__,
        "sanitized_message": sanitize_failure_text(exc, root=root),
        "resume_available": True,
        "created_at_utc": now_utc(),
    }
    if run_id:
        summary["run_id"] = run_id
    if started_at_utc:
        summary["started_at_utc"] = started_at_utc
    if safe_diagnostics:
        summary["incomplete_diagnostics"] = safe_diagnostics
    write_json(
        path,
        summary,
    )
    return path


def require_new_output(path: Path, *, overwrite: bool) -> None:
    if path.exists() and any(path.iterdir()) and not overwrite:
        raise FileExistsError(f"산출물이 이미 존재합니다: {path}; --overwrite를 사용하십시오.")
