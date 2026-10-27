"""Run or dry-run Step08 text-only Pass2 candidate synthesis."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from func.shared.ai_provider import IMAGE_DETAILS, ProviderError, build_provider, provider_defaults, resolve_ai_settings  # noqa: E402
from func.shared.env_loader import load_project_env  # noqa: E402
from func.shared.pipeline_common import setup_pipeline_logging, update_run_status  # noqa: E402
from func.shared.runtime_paths import configured_project_root  # noqa: E402
from func.shared.pass2_common import Pass2PipelineError  # noqa: E402
from func.step08.synthesis import dry_run_summary, run_step08  # noqa: E402

ROOT = configured_project_root(ROOT)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synthesize Step07 check assessments by candidate without images.")
    parser.add_argument("--init", required=True)
    parser.add_argument("--candidate-id")
    parser.add_argument("--dry-run", action="store_true", help="Validate Step08 input and deterministic question IDs without an API call or output write.")
    parser.add_argument("--provider", choices=["openai", "codex_cli", "openai_compatible"], default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--image-detail", choices=sorted(IMAGE_DETAILS), default=None)
    parser.add_argument("--reasoning-effort-pass2", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument("--reasoning-effort-repair", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None, help="Reasoning effort for repeated Step08 semantic/narrative regeneration. Defaults to high.")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--no-env-file", action="store_true")
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--openai-base-url", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--no-resume", action="store_true", help="Ignore reusable Step08 candidate checkpoints for the selected candidate(s).")
    return parser


def _config(args: argparse.Namespace) -> dict[str, str]:
    env_load = load_project_env(ROOT, env_file=args.env_file, enabled=not args.no_env_file)

    # Step08 model selection is controlled only by the project env file.
    if args.model is not None:
        raise ValueError(
            "Step08 model selection is env-file-only; edit AIFS_MODEL_STEP08 in envi/api_keys.env and do not pass --model"
        )
    settings = resolve_ai_settings(
        cli_provider=args.provider,
        cli_model=None,
        cli_image_detail=args.image_detail,
        cli_reasoning_efforts={"pass2_synthesis": args.reasoning_effort_pass2},
        reasoning_defaults={"pass2_synthesis": "medium"},
        env_load=env_load,
        model_env_names=("AIFS_MODEL_STEP08",),
    )
    if settings.model.source != "env_file":
        raise ValueError(
            "Step08 model must come from envi/api_keys.env only; "
            f"resolved source={settings.model.source!r}. Configure AIFS_MODEL_STEP08 in the project env file and remove process-environment model overrides."
        )

    repair_env = {
        "openai": "OPENAI_REASONING_EFFORT_PASS2_SYNTHESIS_REPAIR",
        "codex_cli": "CODEX_REASONING_EFFORT_PASS2_SYNTHESIS_REPAIR",
        "openai_compatible": "OPENAI_COMPATIBLE_REASONING_EFFORT_PASS2_SYNTHESIS_REPAIR",
    }.get(settings.provider.value)
    repair_reasoning = (
        args.reasoning_effort_repair
        or (os.getenv(repair_env) if repair_env else None)
        or "high"
    ).strip()
    if repair_reasoning not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}:
        raise ValueError(f"unsupported Step08 repair reasoning effort: {repair_reasoning}")
    return {
        "provider": settings.provider.value,
        "model": settings.model.value,
        "image_detail": settings.image_detail.value,
        "reasoning_effort": settings.reasoning_efforts["pass2_synthesis"].value,
        "repair_reasoning_effort": repair_reasoning,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logger = setup_pipeline_logging(ROOT, args.init, "step08")
    try:
        config = _config(args)
        summary = dry_run_summary(ROOT, args.init, candidate_id=args.candidate_id)
        candidate_count = len(summary["candidates"])
        ai_processed_count = summary["expected_api_calls"]
        logger.info(
            "STEP08_START init=%s candidate=%s provider=%s model=%s reasoning_effort=%s repair_reasoning_effort=%s candidates=%s max_api_candidates=%s timeout_seconds=%s max_retries=%s candidate_result_reuse=%s",
            args.init,
            args.candidate_id or "ALL",
            config["provider"],
            config["model"],
            config["reasoning_effort"],
            config["repair_reasoning_effort"],
            candidate_count,
            ai_processed_count,
            args.timeout_seconds,
            args.max_retries,
            "disabled" if args.no_resume else "enabled",
        )
        if args.dry_run:
            logger.info(
                "STEP08_DRY_RUN init=%s candidates=%s expected_api_calls=%s",
                args.init,
                candidate_count,
                ai_processed_count,
            )
            print(f"STEP08_DRY_RUN init={args.init} candidate={args.candidate_id or 'ALL'} candidates={len(summary['candidates'])} expected_api_calls={summary['expected_api_calls']}")
            for row in summary["candidates"]:
                print(f"  {row['candidate_id']} checks={row['check_count']} usable_checks={row['usable_check_count']} verified_relations={row['verified_quantitative_relation_count']} api_required={row['api_required']} questions={','.join(row['forecaster_question_ids'])}")
            return 0
        if args.candidate_id is None:
            update_run_status(root=ROOT, init=args.init, step="step08", status="running", summary={
                "candidates": candidate_count,
                "model": config["model"],
                "reasoning_effort": config["reasoning_effort"],
                "repair_reasoning_effort": config["repair_reasoning_effort"],
            })
        defaults = provider_defaults(config["provider"])
        provider = build_provider(provider=config["provider"], base_url=args.base_url, openai_base_url=args.openai_base_url, api_key_env=args.api_key_env or defaults["api_key_env"], timeout_seconds=args.timeout_seconds, max_retries=args.max_retries)
        language_retries: list[tuple[str, int]] = []
        observable_retries: list[tuple[str, int]] = []
        semantic_retries: list[tuple[str, str]] = []
        reference_autofixes: list[tuple[str, int]] = []
        reused_candidates: list[str] = []
        checkpointed_candidates: list[str] = []

        def log_language_retry(candidate: str, violations: list[str], usage: dict | None) -> None:
            attempt = sum(1 for item_candidate, _count in language_retries if item_candidate == candidate) + 1
            language_retries.append((candidate, len(violations)))
            logger.warning(
                "STEP08_LANGUAGE_RETRY init=%s candidate=%s violations=%s usage=%s attempt=%s",
                args.init,
                candidate,
                len(violations),
                usage or {},
                attempt,
            )

        def log_observable_retry(candidate: str, violations: list[str], usage: dict | None) -> None:
            attempt = sum(1 for item_candidate, _count in observable_retries if item_candidate == candidate) + 1
            observable_retries.append((candidate, len(violations)))
            logger.warning(
                "STEP08_OBSERVABLE_RETRY init=%s candidate=%s violations=%s usage=%s attempt=%s",
                args.init,
                candidate,
                len(violations),
                usage or {},
                attempt,
            )

        def log_reference_autofix(candidate: str, fixes: list[str]) -> None:
            reference_autofixes.append((candidate, len(fixes)))
            logger.warning(
                "STEP08_REFERENCE_AUTOFIX init=%s candidate=%s fixes=%s details=%s",
                args.init,
                candidate,
                len(fixes),
                fixes[:8],
            )

        def log_semantic_retry(candidate: str, code: str, error: str, usage: dict | None) -> None:
            attempt = sum(1 for item_candidate, item_code in semantic_retries if item_candidate == candidate and item_code == code) + 1
            semantic_retries.append((candidate, code))
            logger.warning(
                "STEP08_SEMANTIC_RETRY init=%s candidate=%s code=%s usage=%s attempt=%s error=%s",
                args.init,
                candidate,
                code,
                usage or {},
                attempt,
                error,
            )

        def log_candidate_reuse(candidate: str) -> None:
            reused_candidates.append(candidate)
            logger.info(
                "STEP08_CANDIDATE_REUSE init=%s candidate=%s source=validated_checkpoint",
                args.init,
                candidate,
            )

        def log_candidate_checkpoint(candidate: str, completed: int, total: int) -> None:
            checkpointed_candidates.append(candidate)
            logger.info(
                "STEP08_CANDIDATE_CHECKPOINT init=%s candidate=%s completed=%s total=%s",
                args.init,
                candidate,
                completed,
                total,
            )

        result = run_step08(
            root=ROOT,
            init=args.init,
            config=config,
            provider=provider,
            candidate_id=args.candidate_id,
            on_language_retry=log_language_retry,
            on_observable_retry=log_observable_retry,
            on_semantic_retry=log_semantic_retry,
            on_reference_autofix=log_reference_autofix,
            resume=not args.no_resume,
            on_candidate_reuse=log_candidate_reuse,
            on_candidate_checkpoint=log_candidate_checkpoint,
        )
        logger.info(
            "STEP08_OK init=%s candidates=%s max_api_candidates=%s reused_candidates=%s checkpointed_candidates=%s reference_autofix_count=%s semantic_retry_count=%s language_retry_count=%s observable_retry_count=%s synthesis_results=%s candidate_result_reuse=%s",
            args.init,
            candidate_count,
            ai_processed_count,
            len(reused_candidates),
            len(checkpointed_candidates),
            len(reference_autofixes),
            len(semantic_retries),
            len(language_retries),
            len(observable_retries),
            len(result["candidates"]),
            "disabled" if args.no_resume else "enabled",
        )
        if args.candidate_id is None:
            update_run_status(root=ROOT, init=args.init, step="step08", status="completed", summary={
                "candidates": len(result["candidates"]),
                "model": config["model"],
                "reasoning_effort": config["reasoning_effort"],
                "repair_reasoning_effort": config["repair_reasoning_effort"],
                "reused_candidates": len(reused_candidates),
                "checkpointed_candidates": len(checkpointed_candidates),
                "reference_autofixes": len(reference_autofixes),
                "semantic_retries": len(semantic_retries),
                "language_retries": len(language_retries),
                "observable_retries": len(observable_retries),
            })
        print(f"STEP08_OK init={args.init} candidates={len(result['candidates'])}")
        return 0
    except (Pass2PipelineError, ProviderError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, Pass2PipelineError) else "STEP08_FAILED"
        if not args.dry_run and args.candidate_id is None:
            update_run_status(root=ROOT, init=args.init, step="step08", status="failed")
        logger.error("STEP08_FAIL init=%s code=%s error=%s", args.init, code, exc)
        print(f"ERROR [{code}]: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
