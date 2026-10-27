"""Build the validated Step06/Pass2 input package from existing outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from func.shared.runtime_paths import configured_project_root, runtime_path  # noqa: E402
from func.shared.pipeline_common import setup_pipeline_logging, update_run_status  # noqa: E402
from func.step06.package import Pass2PackageError, build_pass2_package  # noqa: E402

ROOT = configured_project_root(ROOT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a validated Step06/Pass2 input package.")
    parser.add_argument("--init", required=True, help="AIFS initialization (YYYYMMDDHH).")
    parser.add_argument("--output", type=Path, help="Output JSON; defaults to daio/<init>/step06/pass2_input_package.json.")
    args = parser.parse_args(argv)
    output = args.output or runtime_path(ROOT, "daio", args.init, "step06", "pass2_input_package.json")
    logger = setup_pipeline_logging(ROOT, args.init, "step06")
    logger.info("STEP06_START init=%s output=%s", args.init, output)
    update_run_status(root=ROOT, init=args.init, step="step06", status="running")
    try:
        package = build_pass2_package(ROOT, args.init)
        candidates = package.get("candidates") if isinstance(package, dict) else []
        candidate_count = len(candidates) if isinstance(candidates, list) else 0
        check_count = sum(
            len(candidate.get("check_evidence", []))
            for candidate in candidates
            if isinstance(candidate, dict) and isinstance(candidate.get("check_evidence"), list)
        )
        logger.info(
            "STEP06_INPUT_READY init=%s candidates=%s checks=%s",
            args.init,
            candidate_count,
            check_count,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp")
        temporary.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)
    except (Pass2PackageError, OSError) as exc:
        code = exc.code if isinstance(exc, Pass2PackageError) else "PACKAGE_WRITE_FAILED"
        update_run_status(root=ROOT, init=args.init, step="step06", status="failed")
        logger.error("STEP06_FAIL init=%s code=%s error=%s", args.init, code, exc)
        print(f"ERROR [{code}]: {exc}", file=sys.stderr)
        return 2
    logger.info(
        "STEP06_OK init=%s candidates=%s checks=%s output=%s",
        args.init,
        candidate_count,
        check_count,
        output,
    )
    update_run_status(root=ROOT, init=args.init, step="step06", status="completed", summary={"candidates": candidate_count, "checks": check_count})
    print(f"PASS2_PACKAGE_OK init={args.init} candidates={len(package['candidates'])} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
