"""Safely retain only recent completed AIFS runtime inputs and intermediates.

Only exact ``dain/<init>`` and ``daio/<init>`` directories are eligible.  A
candidate must be a valid UTC init that is past the retention window and has
both a complete permanent product and an independently published web copy.
``daou``, the web root, and ``logs`` are never cleanup targets.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from func.shared.runtime_paths import configured_project_root  # noqa: E402


INIT_RE = re.compile(r"^\d{10}$")
RUNTIME_TARGETS = ("dain", "daio")
VALID_PRODUCT_STATUSES = frozenset({"published", "no_surface_candidates"})


def parse_init_utc(init: str) -> datetime | None:
    """Return a UTC init timestamp, or ``None`` for a non-calendar init."""
    if INIT_RE.fullmatch(init) is None:
        return None
    try:
        return datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _safe_base(root: Path, name: str) -> Path | None:
    base = root / name
    if not base.exists() or not base.is_dir() or base.is_symlink():
        return None
    try:
        return base if base.resolve().relative_to(root) == Path(name) else None
    except (OSError, ValueError):
        return None


def _safe_target(root: Path, base: Path, init: str) -> Path | None:
    target = base / init
    if target.is_symlink() or not target.exists() or not target.is_dir():
        return None
    try:
        if base.resolve().relative_to(root) != Path(base.name):
            return None
        return target if target.resolve().relative_to(base.resolve()) == Path(init) else None
    except (OSError, ValueError):
        return None


def _runtime_targets(
    root: Path, bases: dict[str, Path | None], init: str
) -> list[dict[str, Any]]:
    """Inspect real runtime directories once per phase, preserving safety state."""
    targets = []
    for name, base in bases.items():
        if base is None:
            continue
        path = base / init
        if path.exists() or path.is_symlink():
            targets.append({
                "target": name,
                "path": str(path),
                "safe": _safe_target(root, base, init) is not None,
            })
    return targets


def _product_files_complete(directory: Path) -> bool:
    """Require three independent, non-symlink published product components."""
    return (
        directory.is_dir()
        and all(
            (directory / name).is_file() and not (directory / name).is_symlink()
            for name in ("index.html", "forecaster_product.json")
        )
        and (directory / "assets").is_dir()
        and not (directory / "assets").is_symlink()
    )


def _publication_reasons(root: Path, web_root: Path, init: str) -> list[str]:
    """Check permanent + independent published copies; never treat links as data."""
    reasons: list[str] = []
    product_dir = root / "daou" / init
    if product_dir.is_symlink() or not _product_files_complete(product_dir):
        reasons.append("final_product_missing")
    else:
        try:
            product = json.loads((product_dir / "forecaster_product.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            reasons.append("final_product_invalid")
        else:
            if (
                not isinstance(product, dict)
                or product.get("init") != init
                or product.get("product_status") not in VALID_PRODUCT_STATUSES
            ):
                reasons.append("final_product_invalid")

    web_dir = web_root / init
    if web_dir.is_symlink():
        # Only a release below the controlled .releases directory may back
        # an atomic web link. Arbitrary external symlinks remain ineligible.
        try:
            published_dir = web_dir.resolve(strict=True)
            published_dir.relative_to((web_root / ".releases").resolve(strict=True))
        except (OSError, ValueError):
            published_dir = None
    else:
        published_dir = web_dir if web_dir.is_dir() else None

    if published_dir is None or not _product_files_complete(published_dir):
        reasons.append("web_product_missing")
    return reasons


def build_cleanup_plan(
    root: Path,
    *,
    web_root: Path,
    retention_days: int,
    now: datetime | None = None,
    current_init: str | None = None,
) -> list[dict[str, Any]]:
    """Plan UTC calendar-date retention without removing any files."""
    if retention_days < 0:
        raise ValueError("retention_days must be zero or greater")
    root = Path(root).resolve()
    web_root = Path(web_root).resolve()
    if current_init is not None and parse_init_utc(current_init) is None:
        raise ValueError("current_init must be a valid YYYYMMDDHH UTC init")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    bases = {name: _safe_base(root, name) for name in RUNTIME_TARGETS}
    unsafe_base = any(
        base is None and ((root / name).exists() or (root / name).is_symlink())
        for name, base in bases.items()
    )
    names: set[str] = set()
    for base in bases.values():
        if base is None:
            continue
        try:
            for child in base.iterdir():
                if child.is_dir() or child.is_symlink():
                    names.add(child.name)
        except OSError:
            continue

    plan: list[dict[str, Any]] = []
    for init in sorted(names):
        init_time = parse_init_utc(init)
        if init_time is None:
            plan.append({"init": init, "action": "SKIP", "reasons": ["invalid_init"], "targets": []})
            continue

        # First classify by UTC calendar day; only expired candidates need
        # expensive publication checks.
        reasons: list[str] = []
        if (now.date() - init_time.date()).days <= retention_days:
            reasons.append("retention_not_expired")
        if init == current_init:
            reasons.append("current_init_protected")

        targets = _runtime_targets(root, bases, init)
        if not targets:
            plan.append({"init": init, "action": "SKIP", "reasons": ["runtime_target_missing"], "targets": []})
            continue

        # One unsafe member or base protects both directories for this init.
        if unsafe_base or any(not target["safe"] for target in targets):
            reasons.append("unsafe_target_path")
        if not reasons:
            reasons.extend(_publication_reasons(root, web_root, init))

        action = "SKIP" if reasons else "DELETE"
        plan.append(
            {
                "init": init,
                "action": action,
                "reasons": reasons,
                "targets": [
                    {**target, "action": action, "reasons": list(reasons)}
                    for target in targets
                ],
            }
        )
    return plan


def _cleanup_logger(root: Path) -> logging.Logger:
    log_path = root / "logs" / "maintenance_cleanup.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"aifsheavyrain.maintenance_cleanup.{root}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == log_path for handler in logger.handlers):
        formatter = logging.Formatter("%(asctime)s UTC %(levelname)s %(message)s")
        formatter.converter = time.gmtime
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
    return logger


def execute_cleanup_plan(
    root: Path,
    plan: list[dict[str, Any]],
    logger: logging.Logger,
    *,
    web_root: Path,
) -> tuple[int, int]:
    """Delete expired targets only after rechecking paths and publication."""
    root, web_root = Path(root).resolve(), Path(web_root).resolve()
    deleted = failures = 0
    for item in plan:
        if item.get("action") != "DELETE":
            continue
        init = str(item["init"])
        delete_targets = [
            row for row in item.get("targets", []) if row.get("action") == "DELETE"
        ]
        if not delete_targets:
            continue

        # Recheck the entire init, including any sibling that appeared since
        # planning. A missing/unsafe planned target also blocks all deletion.
        expected = {row["target"] for row in delete_targets}
        bases = {name: _safe_base(root, name) for name in RUNTIME_TARGETS}
        fresh = {
            row["target"]: row for row in _runtime_targets(root, bases, init)
        }
        unsafe = [
            name for name in RUNTIME_TARGETS
            if (name in expected and name not in fresh)
            or (name in fresh and not fresh[name]["safe"])
        ]
        if not expected.issubset(RUNTIME_TARGETS):
            unsafe.extend(sorted(expected.difference(RUNTIME_TARGETS)))
        unsafe.extend(
            name for name, base in bases.items()
            if base is None and ((root / name).exists() or (root / name).is_symlink())
            and name not in unsafe
        )
        if unsafe:
            logger.error(
                "CLEANUP_FAIL init=%s targets=%s reason=unsafe_target_path; init preserved",
                init, ",".join(unsafe),
            )
            failures += 1
            continue

        # A published copy can disappear or become invalid after plan creation.
        # Reject the entire init if either independent copy is no longer safe.
        publication_errors = _publication_reasons(root, web_root, init)
        if publication_errors:
            logger.error(
                "CLEANUP_FAIL init=%s reason=%s; init preserved",
                init, ",".join(publication_errors),
            )
            failures += 1
            continue

        for name in RUNTIME_TARGETS:
            if name not in expected:
                continue
            try:
                shutil.rmtree(Path(fresh[name]["path"]))
            except OSError as exc:
                logger.error("CLEANUP_FAIL init=%s target=%s error=%s", init, name, exc)
                failures += 1
            else:
                logger.info("CLEANUP_DELETE init=%s target=%s", init, name)
                deleted += 1
    return deleted, failures


def run_cleanup(
    root: Path,
    *,
    web_root: Path,
    retention_days: int,
    dry_run: bool = False,
    current_init: str | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], int]:
    root = Path(root).resolve()
    logger = _cleanup_logger(root)
    now = now or datetime.now(timezone.utc)
    logger.info(
        "CLEANUP_START retention_days=%s dry_run=%s now_utc=%s current_init=%s",
        retention_days,
        str(dry_run).lower(),
        now.astimezone(timezone.utc).isoformat(),
        current_init or "-",
    )
    plan = build_cleanup_plan(
        root,
        web_root=web_root,
        retention_days=retention_days,
        current_init=current_init,
        now=now,
    )
    delete_candidates = 0
    skipped = 0
    for item in plan:
        if not item["targets"]:
            logger.info("CLEANUP_SKIP init=%s reason=%s", item["init"], ",".join(item["reasons"]))
            skipped += 1
            continue
        for target in item["targets"]:
            if target["action"] == "DELETE":
                delete_candidates += 1
                event = "CLEANUP_DRY_RUN_DELETE" if dry_run else "CLEANUP_DELETE_CANDIDATE"
                logger.info("%s init=%s target=%s", event, item["init"], target["target"])
            else:
                logger.info("CLEANUP_SKIP init=%s target=%s reason=%s", item["init"], target["target"], ",".join(target["reasons"]))
                skipped += 1
    if dry_run:
        logger.info("CLEANUP_OK dry_run=true delete_candidates=%s skipped=%s deleted_targets=0", delete_candidates, skipped)
        return plan, 0

    deleted, failures = execute_cleanup_plan(root, plan, logger, web_root=web_root)
    if failures:
        logger.error("CLEANUP_FAIL deleted_targets=%s failed_targets=%s", deleted, failures)
        return plan, 1
    logger.info("CLEANUP_OK dry_run=false delete_candidates=%s skipped=%s deleted_targets=%s", delete_candidates, skipped, deleted)
    return plan, 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely delete expired completed AIFS dain/daio init directories.")
    parser.add_argument("--root", type=Path, default=configured_project_root(PROJECT_ROOT))
    parser.add_argument("--web-root", type=Path, default=Path(os.getenv("AIFS_WEB_ROOT", "/var/www/html")))
    parser.add_argument(
        "--retention-days",
        type=int,
        required=True,
        help="UTC calendar days to retain; provided by run_pipeline.ksh.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan and log cleanup actions without deleting files.")
    parser.add_argument("--current-init", help="Protect this currently running init even if it would otherwise be eligible.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _plan, status = run_cleanup(
            args.root,
            web_root=args.web_root,
            retention_days=args.retention_days,
            dry_run=args.dry_run,
            current_init=args.current_init,
        )
        return status
    except (OSError, ValueError) as exc:
        root = Path(args.root).resolve()
        logger = _cleanup_logger(root)
        logger.error("CLEANUP_FAIL error=%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
