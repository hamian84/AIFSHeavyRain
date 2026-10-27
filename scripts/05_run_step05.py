"""Run the request-driven, downstream-only Step05 operational workflow."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from func.step05.diagnostics import (  # noqa: E402
    AIFSFields,
    Step05DiagnosticError,
    cleanup_generated_image_artifacts,
    cleanup_request_artifacts,
    cleanup_stale_init_artifacts,
    physical_products_for,
    quantitative_statistics,
    render_request_images,
    resolve_request,
    resolve_target,
    result_document,
    validate_request,
    write_result,
    write_spatial_result_atomically,
    load_pass1_result,
)
from func.step05.candidate_overview import build_step05_candidate_overviews  # noqa: E402
from func.shared.pipeline_common import setup_pipeline_logging, update_run_status  # noqa: E402
from func.shared.runtime_paths import configured_project_root, logical_ref, runtime_path  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate only the Step04 Pass1-requested Step05 diagnostics and single-panel images."
    )
    parser.add_argument("--init", required=True, help="AIFS initialization (YYYYMMDDHH).")
    parser.add_argument("--request-id", help="Exact Step04 request_id to run.")
    parser.add_argument("--candidate-id", help="Exact candidate_id; must be paired with --diagnostic-id.")
    parser.add_argument("--diagnostic-id", help="Exact diagnostic_id; must be paired with --candidate-id.")
    parser.add_argument("--forecast-hour", type=int, help="Exact requested F-hour. Nearest-hour substitution is not allowed.")
    parser.add_argument("--overwrite", action="store_true", help="Replace this runner's existing request JSON/PNG outputs.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failed request.")
    parser.add_argument("--inventory-only", action="store_true", help="Classify Step04 requests without reading GRIB or writing Step05 output.")
    parser.add_argument("--root", type=Path, default=configured_project_root(PROJECT_ROOT), help=argparse.SUPPRESS)
    return parser


def _relative(root: Path, path: Path) -> str:
    try:
        return logical_ref(root, path)
    except ValueError:
        return str(path)


def _validate_selector(args: argparse.Namespace) -> None:
    if args.request_id and (args.candidate_id or args.diagnostic_id):
        raise Step05DiagnosticError(
            "REQUEST_SELECTION_INVALID",
            "Use --request-id alone, or use the exact pair --candidate-id and --diagnostic-id.",
        )
    if bool(args.candidate_id) != bool(args.diagnostic_id):
        raise Step05DiagnosticError(
            "REQUEST_SELECTION_INVALID",
            "--candidate-id and --diagnostic-id must be supplied together.",
        )


def _selection(requests: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    _validate_selector(args)
    if args.request_id or args.candidate_id:
        return [resolve_request(
            requests,
            request_id=args.request_id,
            candidate_id=args.candidate_id,
            diagnostic_id=args.diagnostic_id,
        )]
    # Verify a duplicate request_id before any bulk processing.  A bulk loop
    # must never quietly produce two artifacts for one declared logical ID.
    seen: set[str] = set()
    for request in requests:
        request_id = str(request.get("request_id") or "")
        if request_id in seen:
            raise Step05DiagnosticError("REQUEST_ID_DUPLICATE", f"Duplicate request_id in Step04 result: {request_id!r}.")
        seen.add(request_id)
    return list(requests)


def _inventory(
    root: Path,
    init: str,
    payload: dict[str, Any],
    requests: list[dict[str, Any]],
    args: argparse.Namespace,
    *,
    logger: Any,
    started_at: float,
) -> int:
    rows: list[tuple[str, str, str, str]] = []
    counts: Counter[str] = Counter()
    quantitative_requested = 0
    for request in requests:
        request_id = str(request.get("request_id") or "")
        candidate_id = str(request.get("candidate_id") or "")
        diagnostic_id = str(request.get("diagnostic_id") or "")
        quantitative_requested += len(request.get("feature_ids", [])) if isinstance(request.get("feature_ids"), list) else 0
        error_code: str | None = None
        error_message: str | None = None
        try:
            hours = validate_request(request, selected_hour=args.forecast_hour)
            products = physical_products_for(request)
            if str(request.get("data_type") or "") not in {"spatial_field", "quantitative_excerpt"}:
                raise Step05DiagnosticError("DATA_TYPE_UNSUPPORTED", f"Unsupported data_type={request.get('data_type')!r}.")
            # Validate target/domain contracts without opening GRIB.
            resolve_target(root=root, payload=payload, request=request)
            status, detail = "supported", ",".join(products)
            if not hours:
                status, detail = "invalid", "FORECAST_HOURS_INVALID"
        except Step05DiagnosticError as exc:
            status, detail = ("unsupported" if exc.code == "UNSUPPORTED_DIAGNOSTIC" else "invalid"), exc.code
            error_code, error_message = exc.code, str(exc)
        rows.append((request_id, str(request.get("data_type") or ""), str(request.get("diagnostic_id") or ""), f"{status}: {detail}"))
        counts[status] += 1
        log_values = (
            request_id,
            candidate_id,
            diagnostic_id,
            status,
        )
        if error_code is None:
            logger.info(
                "request request_id=%s candidate_id=%s diagnostic_id=%s status=%s generated_png=0 quantitative_feature=0",
                *log_values,
            )
        else:
            logger.error(
                "request request_id=%s candidate_id=%s diagnostic_id=%s status=%s generated_png=0 quantitative_feature=0 "
                "error_code=%s error_message=%s",
                *log_values,
                error_code,
                error_message,
            )
    print("STEP05 REQUEST INVENTORY")
    print(f"  init: {init}")
    print(f"  total: {len(rows)}")
    for key in ("supported", "unsupported", "invalid", "not_requested"):
        print(f"  {key}: {counts[key]}")
    for request_id, data_type, diagnostic_id, status in rows:
        print(f"  {request_id} | {data_type} | {diagnostic_id} | {status}")
    exit_code = 2 if counts["unsupported"] or counts["invalid"] else 0
    logger.info(
        "summary total_requests=%s success=%s failed=0 unsupported=%s invalid=%s "
        "quantitative_requested=%s quantitative_generated=0 generated_png=0 generated_json=0 elapsed_seconds=%.3f",
        len(rows),
        counts["supported"],
        counts["unsupported"],
        counts["invalid"],
        quantitative_requested,
        time.perf_counter() - started_at,
    )
    logger.info("complete init=%s inventory_only=true exit_code=%s", init, exit_code)
    return exit_code


def _result_path(diagnostics_dir: Path, request: dict[str, Any]) -> Path:
    request_id = str(request.get("request_id") or "UNKNOWN")
    return diagnostics_dir / f"{request_id}.json"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    started_at = time.perf_counter()
    logger = setup_pipeline_logging(root, args.init, "step05")
    full_status_run = bool(
        not args.inventory_only
        and not args.request_id
        and not args.candidate_id
        and not args.diagnostic_id
        and args.forecast_hour is None
    )
    try:
        _source, payload, all_requests = load_pass1_result(root, args.init)
        requests = _selection(all_requests, args)
    except Step05DiagnosticError as exc:
        if full_status_run:
            update_run_status(root=root, init=args.init, step="step05", status="failed")
        logger.error("start_failed init=%s error_code=%s error_message=%s", args.init, exc.code, exc)
        print(f"ERROR [{exc.code}]: {exc}", file=sys.stderr)
        return 2

    logger.info(
        "start init=%s overwrite=%s inventory_only=%s step04_request_count=%s",
        args.init,
        bool(args.overwrite),
        bool(args.inventory_only),
        len(all_requests),
    )

    if args.inventory_only:
        return _inventory(root, args.init, payload, requests, args, logger=logger, started_at=started_at)

    if full_status_run:
        update_run_status(root=root, init=args.init, step="step05", status="running", summary={"selected_requests": len(requests)})
    output_root = runtime_path(root, "daio", args.init, "step05")
    images_dir, diagnostics_dir = output_root / "images", output_root / "diagnostics"
    if not requests:
        # A valid no-candidate Step04 produces no Step05 request artifacts, but
        # Step06 still treats these directories as the completed-Step05 boundary.
        images_dir.mkdir(parents=True, exist_ok=True)
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
    full_init_overwrite = bool(
        args.overwrite
        and not args.request_id
        and not args.candidate_id
        and args.forecast_hour is None
    )
    if full_init_overwrite:
        try:
            removed = cleanup_stale_init_artifacts(
                diagnostics_dir=diagnostics_dir,
                images_dir=images_dir,
                live_request_ids=(str(request.get("request_id") or "") for request in all_requests),
            )
        except Step05DiagnosticError as exc:
            if full_status_run:
                update_run_status(root=root, init=args.init, step="step05", status="failed")
            print(f"ERROR [{exc.code}]: {exc}", file=sys.stderr)
            return 2
        if removed:
            print(f"STALE_ARTIFACTS_REMOVED {len(removed)}")
    fields: AIFSFields | None = None
    counts: Counter[str] = Counter()
    quantitative_requested = sum(
        len(request.get("feature_ids", []))
        for request in requests
        if isinstance(request.get("feature_ids"), list)
    )
    quantitative_generated = 0
    generated_png = 0
    generated_json = 0
    for request in requests:
        request_id = str(request.get("request_id") or "UNKNOWN")
        candidate_id = str(request.get("candidate_id") or "")
        diagnostic_id = str(request.get("diagnostic_id") or "")
        result_path = _result_path(diagnostics_dir, request)
        requested_hours = request.get("forecast_hours")
        safe_hours = [int(value) for value in requested_hours] if isinstance(requested_hours, list) and all(isinstance(value, int) for value in requested_hours) else []
        target = None
        generated_images: list[dict[str, Any]] = []
        features: dict[str, Any] = {}
        try:
            if result_path.exists() and not args.overwrite:
                raise Step05DiagnosticError("OUTPUT_EXISTS", f"Output exists: {result_path}; rerun with --overwrite.")
            hours = validate_request(request, selected_hour=args.forecast_hour)
            data_type = str(request.get("data_type") or "")
            if data_type not in {"spatial_field", "quantitative_excerpt"}:
                raise Step05DiagnosticError("DATA_TYPE_UNSUPPORTED", f"Unsupported data_type={data_type!r}.")
            products = physical_products_for(request)
            target = resolve_target(root=root, payload=payload, request=request)
            if fields is None:
                fields = AIFSFields.create(root, args.init)
            if args.overwrite:
                # Scope cleanup strictly to this init and request ID.  This
                # removes stale F-hour files before the rebuilt request writes
                # anything, without touching step05_test or other requests.
                cleanup_request_artifacts(
                    diagnostics_dir=diagnostics_dir,
                    images_dir=images_dir,
                    request_id=request_id,
                )
            if data_type == "quantitative_excerpt":
                features = quantitative_statistics(
                    root=root,
                    payload=payload,
                    request=request,
                    fields=fields,
                    target=target,
                    hours=hours,
                )
                requested_features = {str(item) for item in request.get("feature_ids", [])}
                if set(features) != requested_features:
                    raise Step05DiagnosticError(
                        "QUANTITATIVE_FEATURE_INCOMPLETE",
                        f"Generated features {sorted(features)} differ from requested feature_ids {sorted(requested_features)}.",
                    )
                result = result_document(
                    root=root, request=request, target=target, hours=hours, status="success", features=features,
                )
            else:
                generated_images = render_request_images(
                    root=root, init=args.init, payload=payload, request=request, fields=fields,
                    images_dir=images_dir, hours=hours, overwrite=args.overwrite,
                )
                result = result_document(
                    root=root, request=request, target=target, hours=hours, status="success", generated_images=generated_images,
                )
                write_spatial_result_atomically(
                    root=root,
                    images_dir=images_dir,
                    result_path=result_path,
                    result=result,
                    generated_images=generated_images,
                )
            if data_type == "quantitative_excerpt":
                write_result(root, result_path, result)
            counts["success"] += 1
            quantitative_generated += len(features)
            generated_png += len(generated_images)
            generated_json += 1
            logger.info(
                "request request_id=%s candidate_id=%s diagnostic_id=%s status=success generated_png=%s quantitative_feature=%s",
                request_id,
                candidate_id,
                diagnostic_id,
                len(generated_images),
                len(features),
            )
            print(f"SUCCESS {request_id} -> {_relative(root, result_path)}")
        except Step05DiagnosticError as exc:
            if generated_images:
                cleanup_generated_image_artifacts(
                    root=root,
                    images_dir=images_dir,
                    generated_images=generated_images,
                )
            counts["failed"] += 1
            failure_result = result_document(
                root=root, request=request, target=target, hours=safe_hours, status="failed", failure=exc,
            )
            # A pre-existing result remains untouched unless overwrite was
            # explicit; otherwise a failure record would itself overwrite a
            # prior successful operation.
            if args.overwrite or not result_path.exists():
                try:
                    write_result(root, result_path, failure_result)
                except Step05DiagnosticError as write_exc:
                    print(f"ERROR [{write_exc.code}] while recording {request_id}: {write_exc}", file=sys.stderr)
            print(f"ERROR [{exc.code}] {request_id}: {exc}", file=sys.stderr)
            logger.error(
                "request request_id=%s candidate_id=%s diagnostic_id=%s status=failed generated_png=%s quantitative_feature=%s "
                "error_code=%s error_message=%s",
                request_id,
                candidate_id,
                diagnostic_id,
                len(generated_images),
                len(features),
                exc.code,
                exc,
            )
            if args.fail_fast:
                break

    print("STEP05 OPERATION SUMMARY")
    print(f"  init: {args.init}")
    print(f"  selected_requests: {len(requests)}")
    print(f"  success: {counts['success']}")
    print(f"  failed: {counts['failed']}")
    print(f"  images_dir: {_relative(root, images_dir)}")
    print(f"  diagnostics_dir: {_relative(root, diagnostics_dir)}")
    overview_manifest: dict[str, Any] | None = None
    if full_status_run and not counts["failed"]:
        try:
            overview_manifest = build_step05_candidate_overviews(root=root, init=args.init)
            print(
                "STEP05 CANDIDATE OVERVIEW SUMMARY\n"
                f"  assets: {int(overview_manifest.get('asset_count', 0))}\n"
                f"  manifest: {_relative(root, runtime_path(root, 'daio', args.init, 'step05', 'candidate_overview_data.json'))}"
            )
        except Step05DiagnosticError as exc:
            counts["failed"] += 1
            print(f"ERROR [{exc.code}] step05_candidate_overview: {exc}", file=sys.stderr)
            logger.error("step05_candidate_overview status=failed error_code=%s error_message=%s", exc.code, exc)
    exit_code = 2 if counts["failed"] else 0
    logger.info(
        "summary total_requests=%s success=%s failed=%s unsupported=0 invalid=0 "
        "quantitative_requested=%s quantitative_generated=%s generated_png=%s generated_json=%s "
        "candidate_overview_assets=%s elapsed_seconds=%.3f",
        len(requests),
        counts["success"],
        counts["failed"],
        quantitative_requested,
        quantitative_generated,
        generated_png,
        generated_json,
        int((overview_manifest or {}).get("asset_count", 0)),
        time.perf_counter() - started_at,
    )
    logger.info("complete init=%s inventory_only=false exit_code=%s", args.init, exit_code)
    if full_status_run:
        update_run_status(
            root=root, init=args.init, step="step05",
            status="failed" if exit_code else "completed",
            summary={
                "selected_requests": len(requests),
                "success": counts["success"],
                "failed": counts["failed"],
                "generated_png": generated_png,
                "generated_json": generated_json,
                "candidate_overview_assets": int((overview_manifest or {}).get("asset_count", 0)),
            },
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
