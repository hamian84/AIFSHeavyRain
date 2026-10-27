"""Step02 overview 데이터에서 Step03 Pass1 요청 계약을 생성합니다."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from func.step03.pass1_request import build_batch_plan, build_overview_attachment_manifest, build_support_catalog
from func.shared.runtime_paths import configured_project_root, resolve_logical_ref, runtime_path
from func.shared.pipeline_common import (
    add_output_mode_argument, canonical_hash, load_json, normalize_source_reference_paths, rel,
    require_new_output, resolve_output_mode, setup_pipeline_logging, sha256sum, step_dir,
    update_run_status, validate_init, write_failure_summary, write_json,
)


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml  # type: ignore

    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"YAML 객체가 필요합니다: {path}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Step03 Pass1 요청 계약을 생성합니다.")
    parser.add_argument("--init", required=True)
    parser.add_argument("--root", default=str(configured_project_root(PROJECT_ROOT)))
    parser.add_argument("--overwrite", action="store_true")
    add_output_mode_argument(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    output = step_dir(root, args.init, "step03")
    output_mode = resolve_output_mode(args.output_mode)
    logger = setup_pipeline_logging(root, args.init, "step03")
    try:
        validate_init(args.init)
        overview_path = step_dir(root, args.init, "step02") / "overview_data.json"
        overview = normalize_source_reference_paths(root, load_json(overview_path))
        require_new_output(output, overwrite=args.overwrite)
        if args.overwrite and output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True, exist_ok=True)
        update_run_status(root=root, init=args.init, step="step03", status="running")
        overview_manifest = {"overviews": overview.get("overview_assets", [])}
        attachments_manifest, errors = build_overview_attachment_manifest(root, overview_manifest)
        if errors:
            raise ValueError(f"overview 첨부 매니페스트가 유효하지 않습니다: {errors[:3]}")
        policy = _load_yaml(runtime_path(root, "daba", "ai_pass1", "discovery_execution_policy.yaml"))
        refs = {
            "batch_observation": "prompts/templates/ai_pass1_batch_observation_prompt.ko.md",
            "candidate_discovery": "prompts/templates/ai_pass1_candidate_discovery_prompt.ko.md",
            "candidate_plan": "prompts/templates/ai_pass1_candidate_plan_prompt.ko.md",
            "final_schema": "prompts/schemas/ai_discovery_candidate_response.schema.json",
            "batch_schema": "prompts/schemas/ai_pass1_batch_observation.schema.json",
            "candidate_discovery_schema": "prompts/schemas/ai_pass1_candidate_discovery.schema.json",
            "candidate_plan_schema": "prompts/schemas/ai_pass1_candidate_plan.schema.json",
        }
        batch_plan = build_batch_plan(
            attachment_manifest_ref=rel(root, overview_path), attachment_manifest=attachments_manifest,
            policy=policy, batch_observation_prompt_ref=refs["batch_observation"],
            batch_observation_schema_ref=refs["batch_schema"],
        )
        diagnostic_catalog_path = runtime_path(root, "daba", "analysis", "diagnostic_catalog.yaml")
        if not diagnostic_catalog_path.is_file():
            raise FileNotFoundError("detailed diagnostic catalog is missing")
        diagnostic_catalog = _load_yaml(diagnostic_catalog_path)
        support_catalog = build_support_catalog(
            attachment_manifest=attachments_manifest,
            feature_catalog_ref=rel(root, overview_path),
            feature_catalog=overview.get("feature_catalog", {}),
            diagnostic_catalog=diagnostic_catalog,
        )
        support_catalog["diagnostic_catalog_ref"] = rel(root, diagnostic_catalog_path)
        support_catalog["diagnostic_catalog_sha256"] = sha256sum(diagnostic_catalog_path)
        generation_policy = policy.get("candidate_generation_policy") if isinstance(policy, dict) else None
        peak_policy = generation_policy.get("precipitation_peak_anchor") if isinstance(generation_policy, dict) else None
        minimum_peak_mm = (
            float(peak_policy.get("minimum_regional_maximum_6h_mm", 5.0))
            if isinstance(peak_policy, dict)
            else 5.0
        )
        support_catalog["direct_precipitation_peak_anchor"] = {
            "regional_maximum_6h_mm": minimum_peak_mm
        }
        prompt_hashes = {key: sha256sum(resolve_logical_ref(root, value)) for key, value in refs.items()}
        package_fingerprint = canonical_hash({
            "overview_sha256": sha256sum(overview_path),
            "attachments": attachments_manifest["attachments"],
            "batch_plan": batch_plan["batches"],
            "support_catalog": support_catalog,
            "prompt_refs": refs,
            "prompt_hashes": prompt_hashes,
        })
        package_id = "AIPASS1_" + package_fingerprint[:16].upper()
        attachments_manifest["package_id"] = package_id
        batch_plan["package_id"] = package_id
        support_catalog["package_id"] = package_id
        request = {
            "schema_version": "1.0.0", "package_id": package_id, "init": args.init,
            "overview_data_ref": rel(root, overview_path), "overview_data_sha256": sha256sum(overview_path),
            "attachments": attachments_manifest["attachments"], "attachment_fingerprint": attachments_manifest["attachment_fingerprint"],
            "batch_plan": batch_plan, "support_catalog": support_catalog,
            "feature_catalog": overview["feature_catalog"], "operational_region_catalog": overview["operational_region_catalog"],
            "diagnostic_catalog_ref": rel(root, diagnostic_catalog_path),
            "diagnostic_catalog_sha256": sha256sum(diagnostic_catalog_path),
            "prompt_refs": refs,
            "prompt_hashes": prompt_hashes,
        }
        write_json(output / "pass1_request.json", request)
        update_run_status(root=root, init=args.init, step="step03", status="completed", summary={
            "package_id": package_id, "attachment_count": len(request["attachments"]), "batch_count": batch_plan["batch_count"],
        })
        logger.info("step03 완료 init=%s package_id=%s 첨부수=%s 배치수=%s", args.init, package_id, len(request["attachments"]), batch_plan["batch_count"])
        return 0
    except Exception as exc:  # noqa: BLE001
        update_run_status(root=root, init=args.init, step="step03", status="failed")
        write_failure_summary(root=root, init=args.init, step="step03", exc=exc)
        logger.exception("step03 실패")
        print(f"step03 실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
