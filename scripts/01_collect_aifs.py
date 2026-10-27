"""Step01: 고정 source 대체 순서를 사용하는 경량 ECMWF Open Data AIFS 수집기.

범위
----
- 원시 AIFS GRIB2 파일만 다운로드합니다.
- 기본 모드에서는 예측시간별 파일로 분리하고 낮은 동시성의 병렬 작업자로 내려받습니다.
- 기본 재개 모드에서는 --overwrite가 없으면 기존의 비어 있지 않은 파일을 건너뜁니다.
- 없는 파일마다 다음 고정 순서로 ECMWF Open Data mirror를 시도합니다.
  azure -> ecmwf -> google -> aws
- source 순서는 이 파일의 SOURCE_TRY_ORDER만 제어합니다.
- ingest_manifest.json과 Step01 최소 운영 로그만 기록합니다.

의존성
------
    pip install ecmwf-opendata pyyaml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import re
import ssl
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORT))

from func.shared.pipeline_common import add_output_mode_argument, resolve_output_mode, update_run_status, write_failure_summary
from func.shared.runtime_paths import configured_project_root, resolve_logical_ref, runtime_path

# -----------------------------------------------------------------------------
# 수정 가능한 수집 정책
# -----------------------------------------------------------------------------
# 선호 source 순서가 바뀌면 이 목록을 코드에서 변경합니다.
# source 순서는 CLI로 받지 않습니다. 각 요청은 순서대로 각 source를 한 번씩 시도합니다.
# AWS S3 can apply long internal retries after a 503 Slow Down response. Keep it
# as the final mirror so independent endpoints are tried first.
SOURCE_TRY_ORDER = ["azure", "ecmwf", "google", "aws"]
SOURCE_FALLBACK_SLEEP_SEC = 3.0
MAX_FORECAST_HOUR = 120
PARALLEL_WORKERS_DEFAULT = 2
PARALLEL_WORKERS_MAX = 8

ALLOWED_INIT_HOURS = {"00", "06", "12", "18"}
ALLOWED_SOURCES = {"aws", "azure", "google", "ecmwf"}
ALLOWED_LEVTYPES = {"sfc", "pl"}
DEFAULT_NAMELIST = Path("daba") / "collection" / "aifs_download_namelist.yaml"
SCRIPT_NAME = "scripts/01_collect_aifs.py"
STEP_ID = "step01"
_token_re = re.compile(r"^[A-Za-z0-9_.-]+$")
_thread_local = threading.local()
_fatal_stop = threading.Event()


@dataclass(frozen=True)
class RequestSpec:
    index: int
    request_id: str
    parent_id: str | None
    forecast_hour: int | None
    init: str
    date: str
    hour: int
    name: str
    source_order: list[str]
    model: str
    req_type: str
    levtype: str
    params: list[str]
    steps: list[int]
    levels: list[int] | None
    overwrite: bool
    include_name: bool


@dataclass
class SourceAttempt:
    source: str
    ok: bool
    error_kind: str | None = None
    message: str = ""
    elapsed_seconds: float = 0.0


@dataclass
class RequestResult:
    request_id: str
    parent_id: str | None
    forecast_hour: int | None
    name: str
    status: str
    ok: bool
    target: str
    message: str
    size_bytes: int | None = None
    sha256: str | None = None
    retry_count: int = 0
    elapsed_seconds: float = 0.0
    error_kind: str | None = None
    source: str | None = None
    source_attempts: list[dict[str, Any]] = field(default_factory=list)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def split_init(init: str) -> tuple[str, int]:
    if not re.fullmatch(r"\d{10}", init or ""):
        raise ValueError("--init은 YYYYMMDDHH 형식이어야 합니다.")
    if init[8:] not in ALLOWED_INIT_HOURS:
        raise ValueError("--init 시각은 00, 06, 12, 18 중 하나여야 합니다.")
    return init[:8], int(init[8:])


def as_list(value: Any, name: str, *, integer: bool = False) -> list[Any]:
    if value is None:
        raise ValueError(f"{name} 값이 필요합니다.")
    raw = [v.strip() for v in value.split(",")] if isinstance(value, str) else list(value)
    out: list[Any] = []
    for item in raw:
        if str(item).strip() == "":
            continue
        if integer:
            out.append(int(item))
        else:
            token = str(item).strip()
            if not _token_re.match(token):
                raise ValueError(f"{name}에 유효하지 않은 토큰이 있습니다: {token!r}")
            out.append(token)
    if not out:
        raise ValueError(f"{name}은 비워둘 수 없습니다.")
    return out


def as_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{name}은 true 또는 false여야 합니다.")


def validate_source_order(values: list[str]) -> list[str]:
    if not values:
        raise ValueError("SOURCE_TRY_ORDER는 비워둘 수 없습니다.")
    unknown = [v for v in values if v not in ALLOWED_SOURCES]
    if unknown:
        raise ValueError(f"SOURCE_TRY_ORDER에 지원하지 않는 source가 있습니다: {unknown}")
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text.strip())


def fstep(step: int) -> str:
    return f"f{step:03d}"


def raw_dir(root: Path, init: str) -> Path:
    return runtime_path(root, "dain", init, "raw", "aifs")


def ingest_manifest_path(root: Path, init: str) -> Path:
    return runtime_path(root, "dain", init, "ingest_manifest.json")


def log_path(root: Path, init: str) -> Path:
    return runtime_path(root, "logs", init, "step01.log")


def setup_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s UTC %(levelname)s %(message)s")
    formatter.converter = time.gmtime
    file_handler = logging.FileHandler(path, encoding="utf-8")
    stream_handler = logging.StreamHandler(sys.stdout)
    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)
    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, stream_handler],
        force=True,
    )


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("PyYAML is required. Install: pip install pyyaml") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("requests"), list):
        raise ValueError("namelist에는 requests 목록이 있어야 합니다.")
    return data


def validate_steps(steps: list[int]) -> list[int]:
    steps = sorted(set(steps))
    bad = [s for s in steps if s < 0 or s > MAX_FORECAST_HOUR]
    if bad:
        raise ValueError(f"예측시간이 0~{MAX_FORECAST_HOUR}시간 범위를 벗어났습니다: {bad}")
    return steps


def build_specs(
    namelist: dict[str, Any],
    *,
    init: str,
    overwrite: bool,
    split_by_step: bool,
    max_requests: int | None,
) -> tuple[list[RequestSpec], dict[str, Any]]:
    date, hour = split_init(init)
    defaults = namelist.get("defaults", {}) or {}
    source_order = validate_source_order(SOURCE_TRY_ORDER)
    model = str(defaults.get("model", "aifs-single"))
    req_type = str(defaults.get("type", "fc"))
    steps_default = validate_steps(as_list(defaults.get("steps"), "defaults.steps", integer=True))
    split_by_param_default = as_bool(defaults.get("split_by_param", True), "defaults.split_by_param")
    include_name_default = as_bool(defaults.get("include_request_name_in_filename", True), "defaults.include_request_name_in_filename")
    continue_on_error = as_bool(defaults.get("continue_on_error", True), "defaults.continue_on_error")
    overwrite = overwrite or as_bool(defaults.get("overwrite", False), "defaults.overwrite")

    base: list[RequestSpec] = []
    for item in namelist["requests"]:
        name = safe_name(str(item.get("name", f"request_{len(base)+1:03d}")))
        levtype = str(item.get("levtype", ""))
        if levtype not in ALLOWED_LEVTYPES:
            raise ValueError(f"{name}: levtype은 sfc 또는 pl이어야 합니다.")
        params = as_list(item.get("params"), f"{name}.params")
        levels = as_list(item.get("levels"), f"{name}.levels", integer=True) if levtype == "pl" else None
        steps = validate_steps(as_list(item.get("steps", steps_default), f"{name}.steps", integer=True))
        split_by_param = as_bool(item.get("split_by_param", split_by_param_default), f"{name}.split_by_param")
        include_name = as_bool(item.get("include_request_name_in_filename", include_name_default), f"{name}.include_request_name_in_filename")
        param_groups = [[p] for p in params] if split_by_param else [params]
        for group in param_groups:
            spec_name = f"{name}_{group[0]}" if split_by_param and len(group) == 1 else name
            parent = f"REQ{len(base)+1:03d}"
            base.append(
                RequestSpec(
                    len(base), parent, None, None, init, date, hour, spec_name,
                    source_order, model, req_type, levtype, group, steps, levels,
                    overwrite, include_name,
                )
            )

    specs: list[RequestSpec] = []
    if split_by_step:
        for b in base:
            for step in b.steps:
                specs.append(
                    RequestSpec(
                        len(specs), f"{b.request_id}_{fstep(step).upper()}", b.request_id,
                        step, b.init, b.date, b.hour, b.name, b.source_order, b.model,
                        b.req_type, b.levtype, b.params, [step], b.levels, b.overwrite,
                        b.include_name,
                    )
                )
    else:
        specs = [
            RequestSpec(
                i, b.request_id, b.parent_id, b.forecast_hour, b.init, b.date, b.hour,
                b.name, b.source_order, b.model, b.req_type, b.levtype, b.params,
                b.steps, b.levels, b.overwrite, b.include_name,
            )
            for i, b in enumerate(base)
        ]

    before_limit = len(specs)
    if max_requests:
        specs = specs[:max_requests]
    runtime = {
        "continue_on_error": continue_on_error,
        "split_by_step": split_by_step,
        "source_try_order": source_order,
        "source_fallback_sleep_sec": SOURCE_FALLBACK_SLEEP_SEC,
        "original_request_count_before_step_split": len(base),
        "expanded_request_count_before_limit": before_limit,
        "limited_test_mode": bool(max_requests and max_requests < before_limit),
    }
    return specs, runtime


def filename_for(spec: RequestSpec) -> str:
    params = "-".join(spec.params)
    name = safe_name(spec.name)
    if len(spec.params) == 1 and name.endswith(f"_{spec.params[0]}"):
        name = name[: -(len(spec.params[0]) + 1)]
    name_part = f"_{name}" if spec.include_name and name else ""
    step_part = fstep(spec.steps[0]) if len(spec.steps) == 1 else f"{fstep(min(spec.steps))}_{fstep(max(spec.steps))}"
    if spec.levtype == "pl":
        levels = "-".join(str(v) for v in spec.levels or [])
        return f"aifs_single_{spec.init}_{step_part}_pl_{levels}{name_part}_{params}.grib2"
    return f"aifs_single_{spec.init}_{step_part}_sfc{name_part}_{params}.grib2"


def target_for(root: Path, spec: RequestSpec) -> Path:
    return raw_dir(root, spec.init) / filename_for(spec)


def ecmwf_request(spec: RequestSpec) -> dict[str, object]:
    req: dict[str, object] = {
        "date": spec.date,
        "time": spec.hour,
        "type": spec.req_type,
        "levtype": spec.levtype,
        "param": spec.params,
        "step": spec.steps,
    }
    if spec.levtype == "pl":
        req["levelist"] = spec.levels
    return req


def get_client(source: str, model: str):
    try:
        from ecmwf.opendata import Client  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("ecmwf-opendata is required. Install: pip install ecmwf-opendata") from exc
    key = f"{source}:{model}"
    cache = getattr(_thread_local, "clients", {})
    if key not in cache:
        cache[key] = Client(source=source, model=model, preserve_request_order=True)
        _thread_local.clients = cache
    return cache[key]


def sha256sum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def error_kind(exc: BaseException) -> str:
    text = str(exc).lower()
    if isinstance(exc, ssl.SSLCertVerificationError) or "certificate verify failed" in text or "self-signed certificate" in text:
        return "ssl"
    if "429" in text or "too many requests" in text:
        return "too_many_requests"
    if "503" in text or "slow down" in text:
        return "slow_down"
    if any(s in text for s in ["connectionreset", "connection aborted", "timeout", "temporarily unavailable", "connection refused"]):
        return "transient_network"
    if "400" in text or "bad request" in text:
        return "bad_request"
    if "404" in text or "not found" in text:
        return "not_found"
    return "other"


def make_attempt(source: str, ok: bool, *, kind: str | None = None, message: str = "", elapsed: float = 0.0) -> dict[str, Any]:
    return asdict(SourceAttempt(source=source, ok=ok, error_kind=kind, message=message, elapsed_seconds=round(elapsed, 3)))


def download_one(root: Path, spec: RequestSpec, *, dry_run: bool, hash_file: bool, verify_existing: bool = False) -> RequestResult:
    t0 = time.monotonic()
    target = target_for(root, spec)
    if _fatal_stop.is_set() and not dry_run:
        return RequestResult(spec.request_id, spec.parent_id, spec.forecast_hour, spec.name, "canceled", False, str(target), "canceled after fatal error")
    if dry_run:
        return RequestResult(spec.request_id, spec.parent_id, spec.forecast_hour, spec.name, "planned", True, str(target), "dry-run")
    if target.exists() and target.stat().st_size > 0 and not spec.overwrite:
        return RequestResult(
            spec.request_id, spec.parent_id, spec.forecast_hour, spec.name, "skipped", True,
            str(target), "existing file verified" if verify_existing else "existing file skipped", target.stat().st_size,
            sha256sum(target) if hash_file else None,
            elapsed_seconds=round(time.monotonic() - t0, 3),
        )
    if verify_existing:
        return RequestResult(
            spec.request_id, spec.parent_id, spec.forecast_hour, spec.name, "failed", False,
            str(target), "required existing file is missing", error_kind="not_found",
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    last_kind: str | None = None
    last_message = ""

    for source_index, source in enumerate(spec.source_order, start=1):
        if _fatal_stop.is_set():
            return RequestResult(
                spec.request_id, spec.parent_id, spec.forecast_hour, spec.name, "canceled", False,
                str(target), "canceled after fatal error", retry_count=len(attempts),
                elapsed_seconds=round(time.monotonic() - t0, 3), source_attempts=attempts,
            )
        s0 = time.monotonic()
        try:
            logging.info(
                "다운로드 %s %s source=%s 시도=%s/%s -> %s",
                spec.request_id, spec.name, source, source_index, len(spec.source_order), target.name,
            )
            client = get_client(source, spec.model)
            client.retrieve(request=ecmwf_request(spec), target=str(target))
            if not target.exists() or target.stat().st_size <= 0:
                raise RuntimeError("다운로드는 종료됐지만 파일이 없거나 비어 있습니다.")
            attempts.append(make_attempt(source, True, elapsed=time.monotonic() - s0))
            return RequestResult(
                spec.request_id, spec.parent_id, spec.forecast_hour, spec.name, "downloaded", True,
                str(target), "downloaded", target.stat().st_size,
                sha256sum(target) if hash_file else None,
                retry_count=len(attempts) - 1,
                elapsed_seconds=round(time.monotonic() - t0, 3),
                source=source,
                source_attempts=attempts,
            )
        except Exception as exc:  # noqa: BLE001
            last_kind = error_kind(exc)
            last_message = str(exc)
            attempts.append(make_attempt(source, False, kind=last_kind, message=last_message, elapsed=time.monotonic() - s0))
            logging.warning("source 실패 %s %s source=%s 유형=%s: %s", spec.request_id, spec.name, source, last_kind, exc)
            if last_kind == "ssl":
                _fatal_stop.set()
                break
            if source_index < len(spec.source_order) and SOURCE_FALLBACK_SLEEP_SEC > 0:
                time.sleep(SOURCE_FALLBACK_SLEEP_SEC + random.uniform(0, 1.5))

    return RequestResult(
        spec.request_id, spec.parent_id, spec.forecast_hour, spec.name, "failed", False,
        str(target), last_message or "all sources failed", retry_count=max(0, len(attempts) - 1),
        elapsed_seconds=round(time.monotonic() - t0, 3), error_kind=last_kind,
        source=None, source_attempts=attempts,
    )


def run_all(root: Path, specs: list[RequestSpec], *, workers: int, dry_run: bool, hash_file: bool, submit_sleep: float, continue_on_error: bool, verify_existing: bool = False) -> list[RequestResult]:
    if workers < 1 or workers > PARALLEL_WORKERS_MAX:
        raise ValueError(f"--parallel-workers는 1~{PARALLEL_WORKERS_MAX} 범위여야 합니다.")
    results: list[RequestResult] = []
    pending: dict[Any, RequestSpec] = {}
    iterator = iter(specs)

    def submit_next(pool: ThreadPoolExecutor) -> bool:
        try:
            spec = next(iterator)
        except StopIteration:
            return False
        fut = pool.submit(download_one, root, spec, dry_run=dry_run, hash_file=hash_file, verify_existing=verify_existing)
        pending[fut] = spec
        if submit_sleep > 0 and not dry_run:
            time.sleep(submit_sleep)
        return True

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in range(min(workers, len(specs))):
            submit_next(pool)
        while pending:
            done, _ = wait(set(pending), return_when=FIRST_COMPLETED)
            for fut in done:
                pending.pop(fut, None)
                result = fut.result()
                results.append(result)
                if result.ok or continue_on_error:
                    if not _fatal_stop.is_set():
                        submit_next(pool)
                else:
                    _fatal_stop.set()
            if _fatal_stop.is_set():
                for fut in list(pending):
                    fut.cancel()
                break

    done_ids = {r.request_id for r in results}
    for spec in specs:
        if spec.request_id not in done_ids:
            results.append(
                RequestResult(
                    spec.request_id, spec.parent_id, spec.forecast_hour, spec.name,
                    "canceled", False, str(target_for(root, spec)), "not executed after stop",
                )
            )
    by_order = {s.request_id: s.index for s in specs}
    return sorted(results, key=lambda r: by_order.get(r.request_id, 10**9))


def _request_flags(namelist: dict[str, Any], spec_name: str) -> dict[str, bool]:
    matches = [item for item in namelist.get("requests", []) if spec_name == str(item.get("name")) or spec_name.startswith(f"{item.get('name')}_")]
    item = matches[0] if matches else {}
    return {
        "required_for_discovery": bool(item.get("required_for_discovery", False)),
        "required_for_rule_qa": bool(item.get("required_for_rule_qa", False)),
        "optional": bool(item.get("optional", False)),
    }


def _package_fingerprint(files: list[dict[str, Any]]) -> str:
    canonical = "\n".join(
        f"{x['relative_path']}|{x.get('size_bytes')}|{x.get('sha256')}"
        for x in sorted(files, key=lambda item: item["relative_path"])
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_outputs(root: Path, init: str, namelist_path: Path, namelist: dict[str, Any], specs: list[RequestSpec], runtime: dict[str, Any], results: list[RequestResult], elapsed: float) -> dict[str, Any]:
    ingest_path = ingest_manifest_path(root, init)
    ingest_path.parent.mkdir(parents=True, exist_ok=True)

    statuses = ["downloaded", "skipped", "planned", "failed", "canceled"]
    counts = {k: sum(1 for r in results if r.status == k) for k in statuses}
    error_kinds = ["too_many_requests", "slow_down", "ssl", "transient_network", "bad_request", "not_found", "other"]
    terminal_error_counts = {k: sum(1 for r in results if r.error_kind == k) for k in error_kinds}
    attempt_error_counts = {k: 0 for k in error_kinds}
    source_success_counts = {s: sum(1 for r in results if r.status == "downloaded" and r.source == s) for s in SOURCE_TRY_ORDER}
    source_attempt_counts = {s: 0 for s in SOURCE_TRY_ORDER}
    for result in results:
        for attempt in result.source_attempts:
            src = attempt.get("source")
            if src in source_attempt_counts:
                source_attempt_counts[src] += 1
            kind = attempt.get("error_kind")
            if kind in attempt_error_counts:
                attempt_error_counts[kind] += 1

    forecast_hours = sorted({s for spec in specs for s in spec.steps})
    spec_by_id = {spec.request_id: spec for spec in specs}
    files = []
    for r in results:
        if r.status not in {"downloaded", "skipped"}:
            continue
        spec = spec_by_id[r.request_id]
        flags = _request_flags(namelist, spec.name)
        target = Path(r.target)
        files.append({
            "file_name": Path(r.target).name,
            "size_bytes": r.size_bytes,
            "sha256": r.sha256,
        })
    # 확장된 요청별로 요청 정책을 한 번 해석합니다. 선택 항목 실패는 Step01을 막지 않지만
    # 필수 항목 실패는 계속 차단합니다.
    flags_by_request_id = {
        request_id: _request_flags(namelist, spec.name)
        for request_id, spec in spec_by_id.items()
    }
    missing_results = [r for r in results if not r.ok]
    blocking_missing = [
        r for r in missing_results
        if not flags_by_request_id[r.request_id]["optional"]
    ]
    optional_missing = [
        r for r in missing_results
        if flags_by_request_id[r.request_id]["optional"]
    ]
    discovery_required_missing = [
        r for r in missing_results
        if flags_by_request_id[r.request_id]["required_for_discovery"]
    ]
    rule_qa_required_missing = [
        r for r in missing_results
        if flags_by_request_id[r.request_id]["required_for_rule_qa"]
    ]

    expected_count = len(specs)
    actual_count = len(files)
    dry_run = bool(runtime.get("dry_run", False))

    if dry_run:
        # Dry run은 다운로드 asset 없이 전체 요청 계획을 검증하고 기록합니다. 모든 요청이
        # 계획됐고 설정·실행 오류가 없을 때만 성공합니다.
        if counts["planned"] == expected_count and not missing_results:
            coverage_status = "planned"
        else:
            coverage_status = "blocked"
    elif not missing_results and actual_count == expected_count:
        coverage_status = "complete"
    elif not blocking_missing:
        coverage_status = "complete_with_optional_gaps"
    elif actual_count > 0 and not discovery_required_missing:
        coverage_status = "partial_usable"
    else:
        coverage_status = "blocked"

    success_coverage_statuses = {
        "complete",
        "complete_with_optional_gaps",
        "planned",
    }
    manifest_status = (
        "success"
        if coverage_status in success_coverage_statuses
        else "failed"
    )
    profile = namelist.get("collection_profile", {})
    summary = {
        "request_count": len(specs),
        "downloaded_request_count": counts["downloaded"],
        "skipped_request_count": counts["skipped"],
        "planned_request_count": counts["planned"],
        "failed_request_count": counts["failed"],
        "canceled_request_count": counts["canceled"],
        "split_by_step": runtime["split_by_step"],
        "parallel_workers": runtime["parallel_workers"],
        "source_try_order": runtime["source_try_order"],
        "source_attempt_counts": source_attempt_counts,
        "source_success_counts": source_success_counts,
        "total_retry_count": sum(r.retry_count for r in results),
        "too_many_requests_error_count": terminal_error_counts["too_many_requests"],
        "slow_down_error_count": terminal_error_counts["slow_down"],
        "ssl_error_count": terminal_error_counts["ssl"],
        "bad_request_error_count": terminal_error_counts["bad_request"],
        "attempt_error_counts": attempt_error_counts,
        "elapsed_seconds": round(elapsed, 3),
        "min_forecast_hour": min(forecast_hours) if forecast_hours else None,
        "max_forecast_hour": max(forecast_hours) if forecast_hours else None,
        "forecast_hour_count": len(forecast_hours),
        "original_request_count_before_step_split": runtime["original_request_count_before_step_split"],
        "expanded_request_count_before_limit": runtime["expanded_request_count_before_limit"],
        "blocking_missing_count": len(blocking_missing),
        "discovery_required_missing_count": len(discovery_required_missing),
        "rule_qa_required_missing_count": len(rule_qa_required_missing),
        "optional_missing_count": len(optional_missing),
        "coverage_status": coverage_status,
    }
    manifest = {
        "init": init,
        "step": STEP_ID,
        "script": SCRIPT_NAME,
        "created_at_utc": now_utc(),
        "status": manifest_status,
        "schema_version": "1.0.0",
        "collection_profile_id": str(profile.get("profile_id", "unspecified")),
        "collection_profile_version": str(profile.get("profile_version", "unspecified")),
        "expected_asset_count": expected_count,
        "actual_asset_count": actual_count,
        "required_missing_count": len(blocking_missing),
        "discovery_required_missing_count": len(discovery_required_missing),
        "rule_qa_required_missing_count": len(rule_qa_required_missing),
        "optional_missing_count": len(optional_missing),
        "coverage_status": coverage_status,
        "package_fingerprint": _package_fingerprint([
            {"relative_path": item["file_name"], "size_bytes": item["size_bytes"], "sha256": item["sha256"]}
            for item in files
        ]),
        "forecast_hour_range": {
            "start": summary["min_forecast_hour"], "end": summary["max_forecast_hour"],
            "count": summary["forecast_hour_count"],
        },
        "source_success_counts": source_success_counts,
        "download_summary": summary,
        "summary": {
            key: summary[key]
            for key in ("request_count", "downloaded_request_count", "skipped_request_count", "failed_request_count", "canceled_request_count", "coverage_status")
        },
        "files": files,
    }
    ingest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="고정 source 대체 순서를 사용하는 AIFSHeavyRain Step01 경량 수집기")
    p.add_argument("--init", required=True)
    p.add_argument("--root", default=str(configured_project_root(PROJECT_ROOT_FOR_IMPORT)))
    p.add_argument("--namelist", default=str(DEFAULT_NAMELIST))
    p.add_argument("--parallel-workers", type=int, default=PARALLEL_WORKERS_DEFAULT)
    p.add_argument("--max-requests", type=int, default=None)
    p.add_argument("--request-sleep-sec", type=float, default=0.0, help="제출 작업 사이의 대기 시간(초)입니다. source 순서는 코드에서 제어합니다.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--hash", action="store_true")
    p.add_argument("--rebuild-manifest", action="store_true", help="기존 raw 파일만 검증하고 checksum manifest를 재구성합니다. 다운로드하지 않습니다.")
    p.add_argument("--split-by-step", dest="split_by_step", action="store_true", default=True)
    p.add_argument("--no-split-by-step", dest="split_by_step", action="store_false")
    add_output_mode_argument(p)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.output_mode = resolve_output_mode(args.output_mode)
    _fatal_stop.clear()
    root = Path(args.root).resolve()
    namelist_path = Path(args.namelist)
    if not namelist_path.is_absolute():
        namelist_path = resolve_logical_ref(root, namelist_path)
    setup_logging(log_path(root, args.init))
    t0 = time.monotonic()
    try:
        namelist = load_yaml(namelist_path)
        specs, runtime = build_specs(
            namelist,
            init=args.init,
            overwrite=args.overwrite,
            split_by_step=args.split_by_step,
            max_requests=args.max_requests,
        )
        runtime.update({
            "parallel_workers": args.parallel_workers,
            "request_sleep_sec": args.request_sleep_sec,
            "dry_run": args.dry_run,
            "resume": not args.overwrite,
            "verify_existing_only": args.rebuild_manifest,
        })
    except Exception as exc:  # noqa: BLE001
        logging.error("설정 오류: %s", exc)
        print(f"step01 실패: 설정 오류 - {exc}", file=sys.stderr)
        update_run_status(root=root, init=args.init, step=STEP_ID, status="failed")
        write_failure_summary(root=root, init=args.init, step=STEP_ID, exc=exc)
        return 2

    logging.info(
        "step01 시작 init=%s 요청수=%s 작업자수=%s source_순서=%s",
        args.init, len(specs), args.parallel_workers, ",".join(runtime["source_try_order"]),
    )
    results = run_all(
        root, specs, workers=args.parallel_workers, dry_run=args.dry_run,
        hash_file=args.hash or args.rebuild_manifest, submit_sleep=args.request_sleep_sec,
        continue_on_error=runtime["continue_on_error"],
        verify_existing=args.rebuild_manifest,
    )
    manifest = write_outputs(root, args.init, namelist_path, namelist, specs, runtime, results, time.monotonic() - t0)
    s = manifest["download_summary"]
    print("\nAIFSHeavyRain Step01 요약")
    for key in [
        "request_count", "downloaded_request_count", "skipped_request_count",
        "planned_request_count", "failed_request_count", "canceled_request_count",
        "split_by_step", "parallel_workers", "source_try_order",
        "source_success_counts", "source_attempt_counts", "total_retry_count",
        "too_many_requests_error_count", "slow_down_error_count", "ssl_error_count",
        "bad_request_error_count", "elapsed_seconds", "max_forecast_hour",
    ]:
        print(f"  {key}: {s.get(key)}")
    print(f"  수집 매니페스트: {ingest_manifest_path(root, args.init)}")
    print(f"  로그: {log_path(root, args.init)}")
    if manifest["status"] != "success":
        update_run_status(root=root, init=args.init, step=STEP_ID, status="failed", summary=s)
        print("\nstep01 실패: AIFS 자료 수집을 완료하지 못했습니다.", file=sys.stderr)
        for result in results:
            if not result.ok:
                print(f"- {result.request_id} {result.name}: {result.message}", file=sys.stderr)
        return 1
    update_run_status(root=root, init=args.init, step=STEP_ID, status="completed", summary=s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
