"""Run or dry-run Step07 Pass2 VLM check assessments."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from func.shared.ai_provider import IMAGE_DETAILS, build_provider, provider_defaults, resolve_ai_settings  # noqa: E402
from func.shared.env_loader import load_project_env  # noqa: E402
from func.shared.pipeline_common import setup_pipeline_logging, update_run_status  # noqa: E402
from func.shared.runtime_paths import configured_project_root  # noqa: E402
from func.shared.pass2_common import Pass2PipelineError  # noqa: E402
from func.step07.checks import ENSEMBLE_MEMBER_COUNT, dry_run_summary, load_step06_package, load_step07_results, run_step07  # noqa: E402

ROOT = configured_project_root(ROOT)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assess Step06 evidence bundles by Pass2 check.")
    parser.add_argument("--init", required=True)
    parser.add_argument("--candidate-id")
    parser.add_argument("--dry-run", action="store_true", help="Resolve evidence/fingerprints without API calls or output writes.")
    parser.add_argument("--overwrite", action="store_true", help="Discard reusable active check results and remove stale checks.")
    parser.add_argument("--provider", choices=["openai", "codex_cli", "openai_compatible"], default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--image-detail", choices=sorted(IMAGE_DETAILS), default=None)
    parser.add_argument("--reasoning-effort-pass2", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--no-env-file", action="store_true")
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--openai-base-url", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--max-retries", type=int, default=5)
    return parser


def _config(args: argparse.Namespace) -> dict[str, str]:
    env_load = load_project_env(ROOT, env_file=args.env_file, enabled=not args.no_env_file)
    if args.model is not None:
        raise ValueError(
            "Step07 model selection is env-file-only; edit AIFS_MODEL_STEP07 in envi/api_keys.env and do not pass --model"
        )
    settings = resolve_ai_settings(
        cli_provider=args.provider, cli_model=None, cli_image_detail=args.image_detail,
        cli_reasoning_efforts={"pass2_check": args.reasoning_effort_pass2},
        reasoning_defaults={"pass2_check": "medium"}, env_load=env_load,
        model_env_names=("AIFS_MODEL_STEP07",),
    )
    if settings.model.source != "env_file":
        raise ValueError(
            "Step07 model must come from envi/api_keys.env only; "
            f"resolved source={settings.model.source!r}. Configure AIFS_MODEL_STEP07 in the project env file and remove process-environment model overrides."
        )
    return {
        "provider": settings.provider.value, "model": settings.model.value,
        "image_detail": settings.image_detail.value, "reasoning_effort": settings.reasoning_efforts["pass2_check"].value,
    }


def _reused_check_count(summary: dict[str, object], *, init: str, overwrite: bool) -> int:
    if overwrite:
        return 0
    try:
        existing = load_step07_results(ROOT, init)
    except Pass2PipelineError:
        return 0
    previous = {
        (str(item.get("candidate_id")), str(item.get("check_id"))): item
        for item in existing.get("results", [])
        if isinstance(item, dict)
    }
    rows = summary.get("checks", [])
    if not isinstance(rows, list):
        return 0
    return sum(
        1
        for row in rows
        if isinstance(row, dict)
        and isinstance(previous.get((str(row.get("candidate_id")), str(row.get("check_id")))), dict)
        and previous[(str(row.get("candidate_id")), str(row.get("check_id")))].get("input_fingerprint") == row.get("fingerprint")
    )



def _ensemble_agreement_counts(result: dict[str, object]) -> dict[str, int]:
    counts = {"unanimous": 0, "majority": 0, "unresolved": 0, "deterministic": 0}
    rows = result.get("results", [])
    if not isinstance(rows, list):
        return counts
    for row in rows:
        if not isinstance(row, dict):
            continue
        consensus = row.get("ensemble_consensus")
        if not isinstance(consensus, dict):
            continue
        if consensus.get("mode") == "deterministic_insufficient_evidence":
            counts["deterministic"] += 1
            continue
        agreement = str(consensus.get("assessment_agreement") or "")
        if agreement in counts:
            counts[agreement] += 1
    return counts

def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logger = setup_pipeline_logging(ROOT, args.init, "step07")
    try:
        package = load_step06_package(ROOT, args.init)
        config = _config(args)
        summary = dry_run_summary(package, candidate_id=args.candidate_id, config=config, root=ROOT)
        total_checks = len(summary["checks"])
        reused_checks = _reused_check_count(summary, init=args.init, overwrite=args.overwrite)
        new_checks = total_checks - reused_checks
        logger.info(
            "STEP07_START init=%s candidate=%s provider=%s model=%s reasoning_effort=%s ensemble_members=%s checks=%s reused_checks=%s new_checks=%s overwrite=%s",
            args.init,
            args.candidate_id or "ALL",
            config["provider"],
            config["model"],
            config["reasoning_effort"],
            ENSEMBLE_MEMBER_COUNT,
            total_checks,
            reused_checks,
            new_checks,
            args.overwrite,
        )
        if args.dry_run:
            logger.info(
                "STEP07_DRY_RUN init=%s checks=%s bundles=%s expected_api_calls=%s",
                args.init,
                total_checks,
                len(summary["bundles"]),
                summary["expected_api_calls"],
            )
            print(f"STEP07_DRY_RUN init={args.init} candidate={args.candidate_id or 'ALL'} checks={len(summary['checks'])} bundles={len(summary['bundles'])} expected_api_calls={summary['expected_api_calls']} output_schema_valid={summary['output_schema_valid']}")
            for check in summary["checks"]:
                print(f"  {check['candidate_id']} {check['check_id']} requests={','.join(check['request_ids']) or '-'} evidence={check['evidence_status']} scope={check.get('check_scope') or '-'} missing_components={','.join(check.get('missing_signal_components') or []) or '-'} bundle={check['bundle_id']} fingerprint={check['fingerprint'][:16]} cross_section={bool(check['cross_section_context'])} time_height={bool(check['time_height_context'])}")
            for bundle in summary["bundles"]:
                print(f"  BUNDLE {bundle['bundle_id']} checks={','.join(bundle['check_ids'])} png={bundle['png_count']} quantitative={bundle['quantitative_request_count']}")
            return 0
        if args.candidate_id is None:
            update_run_status(root=ROOT, init=args.init, step="step07", status="running", summary={"checks": total_checks})
        defaults = provider_defaults(config["provider"])
        provider = build_provider(
            provider=config["provider"], base_url=args.base_url, openai_base_url=args.openai_base_url,
            api_key_env=args.api_key_env or defaults["api_key_env"], timeout_seconds=args.timeout_seconds, max_retries=args.max_retries,
        )
        grounding_retries: list[tuple[str, str, str]] = []
        language_retries: list[tuple[str, str, int]] = []
        quantitative_selector_dedup_count = 0
        quantitative_relation_selector_autofix_count = 0
        quantitative_relation_autocorrect_count = 0
        quantitative_relation_failures: list[tuple[str, str, str]] = []

        def log_grounding_retry(candidate: str, bundle: str, reason: str, attempt: int) -> None:
            grounding_retries.append((candidate, bundle, reason))
            logger.warning(
                "STEP07_GROUNDING_RETRY init=%s candidate=%s bundle=%s reason=%s attempt=%s",
                args.init,
                candidate,
                bundle,
                reason,
                attempt,
            )


        def log_language_retry(candidate: str, bundle: str, violations: list[str], usage: dict | None) -> None:
            language_retries.append((candidate, bundle, len(violations)))
            logger.warning(
                "STEP07_LANGUAGE_RETRY init=%s candidate=%s bundle=%s violations=%s usage=%s attempt=1",
                args.init,
                candidate,
                bundle,
                len(violations),
                usage or {},
            )

        def log_quantitative_selector_dedup(candidate: str, bundle: str, check_id: str, removed_count: int) -> None:
            nonlocal quantitative_selector_dedup_count
            quantitative_selector_dedup_count += removed_count
            logger.warning(
                "STEP07_QUANTITATIVE_SELECTOR_DEDUP init=%s candidate=%s bundle=%s check=%s removed=%s",
                args.init,
                candidate,
                bundle,
                check_id,
                removed_count,
            )

        def log_quantitative_relation_selector_autofix(candidate: str, bundle: str, check_id: str, added_count: int) -> None:
            nonlocal quantitative_relation_selector_autofix_count
            quantitative_relation_selector_autofix_count += added_count
            logger.warning(
                "STEP07_RELATION_SELECTOR_AUTOFIX init=%s candidate=%s bundle=%s check=%s added=%s",
                args.init,
                candidate,
                bundle,
                check_id,
                added_count,
            )

        def log_quantitative_relation_autocorrect(
            candidate: str, bundle: str, check_id: str, relation_id: str, claimed: str, computed: str
        ) -> None:
            nonlocal quantitative_relation_autocorrect_count
            quantitative_relation_autocorrect_count += 1
            logger.warning(
                "STEP07_RELATION_AUTOCORRECT init=%s candidate=%s bundle=%s check=%s relation=%s claimed=%s computed=%s",
                args.init,
                candidate,
                bundle,
                check_id,
                relation_id,
                claimed,
                computed,
            )

        def log_quantitative_relation_failure(candidate: str, bundle: str, reason: str) -> None:
            quantitative_relation_failures.append((candidate, bundle, reason))
            logger.error(
                "STEP07_QUANTITATIVE_RELATION_FAILED init=%s candidate=%s bundle=%s reason=%s",
                args.init,
                candidate,
                bundle,
                reason,
            )

        result = run_step07(
            root=ROOT,
            init=args.init,
            package=package,
            config=config,
            provider=provider,
            candidate_id=args.candidate_id,
            overwrite=args.overwrite,
            on_grounding_retry=log_grounding_retry,
            on_quantitative_selector_dedup=log_quantitative_selector_dedup,
            on_quantitative_relation_selector_autofix=log_quantitative_relation_selector_autofix,
            on_quantitative_relation_autocorrect=log_quantitative_relation_autocorrect,
            on_language_retry=log_language_retry,
            on_quantitative_relation_failure=log_quantitative_relation_failure,
        )
        agreement_counts = _ensemble_agreement_counts(result)
        logger.info(
            "STEP07_OK init=%s checks=%s reused_checks=%s new_checks=%s ensemble_members=%s consensus_unanimous=%s consensus_majority=%s consensus_unresolved=%s deterministic_checks=%s grounding_retry_count=%s language_retry_count=%s quantitative_selector_dedup_count=%s quantitative_relation_selector_autofix_count=%s quantitative_relation_autocorrect_count=%s quantitative_relation_failure_count=%s final_result_checks=%s",
            args.init,
            total_checks,
            reused_checks,
            new_checks,
            ENSEMBLE_MEMBER_COUNT,
            agreement_counts["unanimous"],
            agreement_counts["majority"],
            agreement_counts["unresolved"],
            agreement_counts["deterministic"],
            len(grounding_retries),
            len(language_retries),
            quantitative_selector_dedup_count,
            quantitative_relation_selector_autofix_count,
            quantitative_relation_autocorrect_count,
            len(quantitative_relation_failures),
            len(result["results"]),
        )
        if args.candidate_id is None:
            update_run_status(
                root=ROOT, init=args.init, step="step07", status="completed",
                summary={
                    "checks": len(result["results"]),
                    "ensemble_member_count": ENSEMBLE_MEMBER_COUNT,
                    "consensus_unanimous": agreement_counts["unanimous"],
                    "consensus_majority": agreement_counts["majority"],
                    "consensus_unresolved": agreement_counts["unresolved"],
                    "deterministic_checks": agreement_counts["deterministic"],
                    "grounding_retries": len(grounding_retries),
                    "language_retries": len(language_retries),
                    "quantitative_relation_selector_autofix_count": quantitative_relation_selector_autofix_count,
                    "quantitative_relation_autocorrect_count": quantitative_relation_autocorrect_count,
                    "quantitative_relation_failures": len(quantitative_relation_failures),
                },
            )
        print(
            f"STEP07_OK init={args.init} checks={len(result['results'])} "
            f"consensus=3/3:{agreement_counts['unanimous']} "
            f"2/3:{agreement_counts['majority']} unresolved:{agreement_counts['unresolved']}"
        )
        return 0
    except (Pass2PipelineError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, Pass2PipelineError) else "STEP07_FAILED"
        if not args.dry_run and args.candidate_id is None:
            update_run_status(root=ROOT, init=args.init, step="step07", status="failed")
        logger.error("STEP07_FAIL init=%s code=%s error=%s", args.init, code, exc)
        print(f"ERROR [{code}]: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
