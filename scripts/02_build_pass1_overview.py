"""원시 AIFS GRIB에서 단일 Step02 overview 데이터 패키지를 직접 생성합니다."""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from func.shared import aifs_data
from func.shared.runtime_paths import configured_project_root, runtime_dir, runtime_path
from func.shared.pipeline_common import (
    add_output_mode_argument, load_json, normalize_source_reference_paths, rel, require_new_output,
    resolve_output_mode, setup_pipeline_logging, sha256sum, step_dir, step_workspace,
    update_run_status, validate_init, write_failure_summary, write_json,
)


_ENGINE_FORECAST_HOUR_RE = re.compile(r"\bfeature=\S+\s+step=f(?P<hour>\d{3})\b")


def _report_progress(logger: logging.Logger, message: str) -> None:
    """간결하고 안전한 Step02 진행 현황을 터미널과 Step02 로그에 함께 기록합니다."""
    logger.info("step02 %s", message)
    print(f"step02 {message}", flush=True)


def _child_engine_environment(root: Path) -> dict[str, str]:
    """Provide the isolated engine with the authoritative project package root."""
    child_env = os.environ.copy()
    project_root = str(configured_project_root(root))
    existing_pythonpath = child_env.get("PYTHONPATH")
    child_env["PYTHONPATH"] = (
        os.pathsep.join((project_root, existing_pythonpath))
        if existing_pythonpath
        else project_root
    )
    return child_env


def _run_calculation_engine(
    *,
    root: Path,
    temp_root: Path,
    init: str,
    raw_dir: Path,
    generated: Path,
    forecast_hours: list[int],
    logger: logging.Logger,
    output_mode: str = "operational",
) -> None:
    """기존 계산 엔진을 실행하되 단계 수준의 진행 현황만 노출합니다."""
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(PROJECT_ROOT / "func" / "step02" / "aifs_feature_engine.py"),
            *_engine_args(root, temp_root, init, raw_dir, generated, output_mode),
        ],
        cwd=temp_root,
        env=_child_engine_environment(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if process.stdout is None:
        raise RuntimeError("overview 계산 엔진의 출력 스트림을 사용할 수 없습니다.")

    hour_positions = {hour: index + 1 for index, hour in enumerate(forecast_hours)}
    last_reported_hour: int | None = None
    for line in process.stdout:
        match = _ENGINE_FORECAST_HOUR_RE.search(line)
        if match is None:
            continue
        hour = int(match.group("hour"))
        if hour == last_reported_hour or hour not in hour_positions:
            continue
        _report_progress(
            logger,
            f"단계=feature_계산 예측시간=F{hour:03d} "
            f"진행={hour_positions[hour]}/{len(forecast_hours)}",
        )
        last_reported_hour = hour

    returncode = process.wait()
    if returncode:
        raise RuntimeError(f"overview 계산 엔진이 종료 코드 {returncode}로 실패했습니다.")


def _engine_args(
    root: Path,
    temp_root: Path,
    init: str,
    raw_dir: Path,
    out: Path,
    output_mode: str,
) -> list[str]:
    config = runtime_dir(root, "daba")
    return [
        "--init", init, "--root", str(temp_root), "--raw-dir", str(raw_dir),
        "--output-dir", str(out), "--operational-regions-file", str(config / "ai_pass1" / "discovery_operational_regions.yaml"),
        "--operational-feature-policy-file", str(config / "ai_pass1" / "operational_region_feature_policy.yaml"),
        "--feature-file", str(config / "ai_pass1" / "feature_definitions.yaml"),
        "--field-summary-policy-file", str(config / "ai_pass1" / "field_summary_policy.yaml"),
        "--product-catalog-file", str(config / "ai_pass1" / "product_catalog.yaml"),
        "--broad-map-products-file", str(config / "ai_pass1" / "broad_map_products.yaml"),
        "--discovery-source-panel-products-file", str(config / "ai_pass1" / "discovery_source_panel_products.yaml"),
        "--discovery-spatial-grid-file", str(config / "ai_pass1" / "discovery_spatial_grid.yaml"),
        "--discovery-execution-policy-file", str(config / "ai_pass1" / "discovery_execution_policy.yaml"),
        "--output-mode", output_mode,
        "--generate-overviews",
    ]


def _overview_data(root: Path, init: str, generated: Path, destination: Path, preflight: dict[str, Any]) -> dict[str, Any]:
    names = {
        "feature_timeseries": "feature_timeseries.json",
        "operational_region_timeseries": "operational_region_timeseries.compact.json",
        "feature_catalog": "discovery_feature_catalog.json",
        "product_catalog": "product_catalog.json",
        "operational_region_catalog": "operational_region_catalog.json",
        "source_panels": "discovery_source_panel_manifest.json",
        "overview_manifest": "discovery_overview_manifest.json",
    }
    loaded = {key: load_json(generated / name) for key, name in names.items()}
    copied: list[dict[str, Any]] = []
    overview_dir = destination / "overview"
    overview_dir.mkdir(parents=True, exist_ok=True)
    for entry in loaded["overview_manifest"].get("overviews", []):
        if not isinstance(entry, dict) or str(entry.get("status")) not in {"created", "created_with_missing_panels"}:
            continue
        source = generated / "discovery_overviews" / Path(str(entry["image_path"])).name
        if not source.is_file():
            raise FileNotFoundError(f"필수 overview 이미지가 없습니다: {source.name}")
        target = overview_dir / source.name
        shutil.copy2(source, target)
        item = dict(entry)
        item.update({
            "image_path": rel(root, target), "file_name": target.name,
            "size_bytes": target.stat().st_size, "sha256": sha256sum(target),
        })
        copied.append(item)
    if len(copied) != 26:
        raise RuntimeError(f"overview 이미지는 26개여야 하나 {len(copied)}개를 찾았습니다.")
    return {
        "schema_version": "1.0.0", "init": init, "preflight": preflight,
        "feature_timeseries": loaded["feature_timeseries"],
        "operational_region_timeseries": loaded["operational_region_timeseries"],
        "feature_catalog": loaded["feature_catalog"],
        "product_catalog": loaded["product_catalog"],
        "operational_region_catalog": loaded["operational_region_catalog"],
        "source_panels": loaded["source_panels"],
        "overview_assets": copied,
        "overview_count": len(copied),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="원시 AIFS GRIB에서 Step02 Pass1 overview 데이터를 생성합니다.")
    parser.add_argument("--init", required=True)
    parser.add_argument("--root", default=str(configured_project_root(PROJECT_ROOT)))
    parser.add_argument("--raw-dir", default=None)
    parser.add_argument("--overwrite", action="store_true")
    add_output_mode_argument(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    output = step_dir(root, args.init, "step02")
    output_mode = resolve_output_mode(args.output_mode)
    logger = setup_pipeline_logging(root, args.init, "step02")
    try:
        validate_init(args.init)
        raw_dir = Path(args.raw_dir).resolve() if args.raw_dir else runtime_path(root, "dain", args.init, "raw", "aifs")
        _report_progress(logger, f"시작 init={args.init}")
        _report_progress(logger, "단계=원시입력_사전검증 시작")
        preflight = aifs_data.preflight_raw_inputs(root=root, init=args.init, raw_dir=raw_dir)
        status_path = runtime_path(root, "daio", args.init, "run_status.json")
        existing_status = load_json(status_path) if status_path.is_file() else {}
        step01_status = str(
            ((existing_status.get("steps") or {}).get("step01") or {}).get("status", "not_started")
        )
        if step01_status == "not_started":
            update_run_status(
                root=root,
                init=args.init,
                step="step01",
                status="reused",
                summary={
                    "reuse_basis": "existing_raw_inputs",
                    "raw_file_count": preflight["raw_file_count"],
                    "indexed_field_reference_count": preflight["indexed_field_reference_count"],
                },
            )
        _report_progress(
            logger,
            "단계=원시입력_사전검증 완료 "
            f"원시파일={preflight['raw_file_count']} "
            f"field_참조={preflight['indexed_field_reference_count']} "
            f"예측시간수={len(preflight['forecast_hours'])}",
        )
        require_new_output(output, overwrite=args.overwrite)
        if args.overwrite and output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True, exist_ok=True)
        update_run_status(root=root, init=args.init, step="step02", status="running")
        with step_workspace(
            root=root,
            init=args.init,
            step="step02",
            output_mode=output_mode,
        ) as temp_root:
            generated = temp_root / "generated"
            # 계산 엔진은 자신의 root에서 style·map resource를 해석합니다. 임시 작업공간을
            # 제거하기 전에 GRIB·지도 처리 handle을 닫기 위해 자식 프로세스로 실행합니다.
            _report_progress(logger, "단계=계산_및_overview_렌더링 시작")
            # On Windows, tempfile.mkdtemp() workspaces can reject the first
            # child-directory creation performed internally by copytree().
            # Create the destination explicitly, then merge the read-only
            # runtime resources into it.
            workspace_daba = temp_root / "daba"
            workspace_daba.mkdir(parents=True, exist_ok=True)
            shutil.copytree(runtime_dir(root, "daba"), workspace_daba, dirs_exist_ok=True)
            _run_calculation_engine(
                root=root,
                temp_root=temp_root,
                init=args.init,
                raw_dir=raw_dir,
                generated=generated,
                forecast_hours=preflight["forecast_hours"],
                logger=logger,
                output_mode=output_mode,
            )
            _report_progress(logger, "단계=계산_및_overview_렌더링 완료")
            _report_progress(logger, "단계=overview_패키지_검증 시작")
            data = _overview_data(root, args.init, generated, output, preflight)
            _report_progress(logger, "단계=overview_패키지_검증 완료")
        data = normalize_source_reference_paths(root, data)
        write_json(output / "overview_data.json", data)
        update_run_status(root=root, init=args.init, step="step02", status="completed", summary={
            "overview_count": 26, "source_panel_count": int(data["source_panels"].get("source_panel_count", 0)),
            "indexed_field_reference_count": preflight["indexed_field_reference_count"],
        })
        logger.info("step02 완료 init=%s overview_count=26", args.init)
        print(
            "step02 완료 "
            f"overview_수={data['overview_count']} "
            f"source_panel_수={data['source_panels'].get('source_panel_count', 0)}",
            flush=True,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        update_run_status(root=root, init=args.init, step="step02", status="failed")
        write_failure_summary(root=root, init=args.init, step="step02", exc=exc)
        logger.exception("step02 실패")
        print(f"step02 실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
