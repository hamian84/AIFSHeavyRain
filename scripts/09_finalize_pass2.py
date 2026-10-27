"""Run deterministic Step09 validation and operational surfacing."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from func.shared.pass2_common import Pass2PipelineError  # noqa: E402
from func.shared.pipeline_common import setup_pipeline_logging, update_run_status  # noqa: E402
from func.shared.runtime_paths import configured_project_root  # noqa: E402
from func.step09.operational import finalize_step09, step09_output_path  # noqa: E402

ROOT = configured_project_root(ROOT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Step08 Pass2 output and determine operational surfacing.")
    parser.add_argument("--init", required=True)
    args = parser.parse_args(argv)
    logger = setup_pipeline_logging(ROOT, args.init, "step09")
    logger.info("STEP09_START init=%s output=%s", args.init, step09_output_path(ROOT, args.init))
    update_run_status(root=ROOT, init=args.init, step="step09", status="running")
    try:
        result = finalize_step09(root=ROOT, init=args.init)
        candidates = result["candidates"]
        surfaced = sum(1 for candidate in candidates if candidate.get("should_surface") is True)
        valid = sum(1 for candidate in candidates if candidate.get("validation_status") == "valid")
        blocked_candidates = [
            candidate
            for candidate in candidates
            if candidate.get("validation_status") == "blocked"
        ]
        for candidate in blocked_candidates:
            logger.warning(
                "STEP09_CANDIDATE_BLOCKED init=%s candidate=%s reason=%s",
                args.init,
                candidate.get("candidate_id"),
                candidate.get("validation_reason"),
            )
        metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        logger.info(
            "STEP09_METRICS init=%s checks=%s usable_checks=%s excluded_checks=%s candidate_time_coverage_hours=%s precursor_only=%s",
            args.init,
            metrics.get("check_count", 0),
            metrics.get("usable_check_count", 0),
            metrics.get("excluded_check_count", 0),
            metrics.get("candidate_time_coverage_hours", 0),
            metrics.get("precursor_only_discovery_count", 0),
        )
        # Step10 requires every active candidate to be valid.  Do not report
        # STEP09_OK when the already-written Step09 result contains a blocked
        # candidate: an operator would otherwise proceed into a guaranteed
        # publication failure and mistake a validation block for success.
        if blocked_candidates:
            failed_ids = ",".join(str(row.get("candidate_id")) for row in blocked_candidates)
            logger.error(
                "STEP09_PUBLICATION_GATE_BLOCKED init=%s blocked=%s candidates=%s "
                "action=repair_step08_then_rerun_step09",
                args.init, len(blocked_candidates), failed_ids,
            )
            update_run_status(
                root=ROOT, init=args.init, step="step09", status="failed",
                summary={
                    "candidates": len(candidates), "valid": valid,
                    "blocked": len(blocked_candidates), "surfaced": surfaced,
                },
            )
            print(
                f"ERROR [STEP09_PUBLICATION_GATE_BLOCKED]: init={args.init} "
                f"blocked={len(blocked_candidates)} candidates={failed_ids}; "
                "Step08 오류 후보를 재검증하고 Step09를 다시 실행하십시오.",
                file=sys.stderr,
            )
            return 2
        logger.info(
            "STEP09_OK init=%s candidates=%s valid_candidates=%s blocked_candidates=%s surfaced_candidates=%s non_surfaced_candidates=%s output=%s",
            args.init,
            len(candidates),
            valid,
            len(blocked_candidates),
            surfaced,
            len(candidates) - surfaced,
            step09_output_path(ROOT, args.init),
        )
        update_run_status(
            root=ROOT,
            init=args.init,
            step="step09",
            status="completed",
            summary={
                "candidates": len(candidates),
                "valid": valid,
                "blocked": len(blocked_candidates),
                "surfaced": surfaced,
                "excluded_checks": metrics.get("excluded_check_count", 0),
                "candidate_time_coverage_hours": metrics.get("candidate_time_coverage_hours", 0),
            },
        )
        print(
            f"STEP09_OK init={args.init} candidates={len(candidates)} "
            f"valid={valid} blocked={len(blocked_candidates)} surfaced={surfaced}"
        )
        return 0
    except (Pass2PipelineError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, Pass2PipelineError) else "STEP09_FAILED"
        update_run_status(root=ROOT, init=args.init, step="step09", status="failed")
        logger.error("STEP09_FAIL init=%s code=%s error=%s", args.init, code, exc)
        print(f"ERROR [{code}]: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
