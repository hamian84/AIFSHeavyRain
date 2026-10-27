"""Run deterministic Step10 forecaster-product publication."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from func.step10.analysis_history import embed_history_comparison, register_analysis_history  # noqa: E402
from func.step10.llm_wiki import load_latest_wiki_for_current_comparison  # noqa: E402
from func.shared.pass2_common import Pass2PipelineError  # noqa: E402
from func.shared.pipeline_common import setup_pipeline_logging, update_run_status  # noqa: E402
from func.shared.runtime_paths import configured_project_root  # noqa: E402
from func.step10.forecaster_product import generate_forecaster_product  # noqa: E402

ROOT = configured_project_root(ROOT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish one deterministic Step10 forecaster product.")
    parser.add_argument("--init", required=True)
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing validated permanent product only after the new package passes validation.")
    args = parser.parse_args(argv)
    logger = setup_pipeline_logging(ROOT, args.init, "step10")
    update_run_status(root=ROOT, init=args.init, step="step10", status="running")
    try:
        product = generate_forecaster_product(ROOT, args.init, overwrite=args.overwrite)
        summary = product["summary"]
        history_status = "not_attempted"
        history_analysis_id = None
        try:
            history_snapshot, history_status = register_analysis_history(ROOT, args.init)
            history_analysis_id = history_snapshot["analysis_id"]
            existing_wiki = load_latest_wiki_for_current_comparison(ROOT, args.init)
            history_ui = embed_history_comparison(ROOT, args.init, wiki_explanation=existing_wiki)
            logger.info(
                "analysis history 등록 완료 init=%s status=%s analysis_id=%s snapshots=%s comparison_embedded=%s llm_wiki_embedded=%s",
                args.init, history_status, history_analysis_id, history_ui["snapshot_count"], history_ui["embedded"], history_ui.get("llm_wiki_embedded", False),
            )
        except (Pass2PipelineError, OSError, ValueError) as history_exc:
            history_status = "warning"
            logger.warning("analysis history 등록 실패 init=%s error=%s", args.init, history_exc)
            print(f"HISTORY_WARNING init={args.init} error={history_exc}", file=sys.stderr)
        update_run_status(root=ROOT, init=args.init, step="step10", status="completed", summary={"candidates": summary["candidate_count"], "surfaced": summary["surfaced_candidate_count"], "assets": len(product["assets"]), "history": history_status})
        print(
            "STEP10_OK"
            f" init={product['init']}"
            f" status={product['product_status']}"
            f" candidates={summary['candidate_count']}"
            f" surfaced={summary['surfaced_candidate_count']}"
            f" assets={len(product['assets'])}"
            f" history={history_status}"
            + (f" analysis_id={history_analysis_id}" if history_analysis_id else "")
        )
        return 0
    except (Pass2PipelineError, OSError, ValueError) as exc:
        update_run_status(root=ROOT, init=args.init, step="step10", status="failed")
        code = exc.code if isinstance(exc, Pass2PipelineError) else "STEP10_FAILED"
        logger.error("STEP10_FAIL init=%s code=%s error=%s", args.init, code, exc)
        print(f"ERROR [{code}]: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
