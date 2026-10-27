"""Safely retain only recent completed AIFS runtime inputs and intermediates.

Only exact ``dain/<init>`` and ``daio/<init>`` directories are eligible.  A
candidate must be a valid UTC init that is past the retention window and has
both a complete permanent product and an independently published web copy.
``daou``, the web root, and ``logs`` are never cleanup targets.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from func.shared.runtime_paths import configured_project_root  # noqa: E402


INIT_RE = re.compile(r"^\d{10}$")
RUNTIME_TARGETS = ("dain", "daio")
VALID_PRODUCT_STATUSES = frozenset({"published", "no_surface_candidates"})
DEFAULT_RETENTION_DAYS = 4


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


def _publication_reasons(root: Path, web_root: Path, init: str) -> list[str]:
    reasons: list[str] = []
    product_dir = root / "daou" / init
    product_path = product_dir / "forecaster_product.json"
    product_index = product_dir / "index.html"
    product_assets = product_dir / "assets"

    # 영구제품 디렉터리 자체가 symlink이면 자식 파일이 일반 파일처럼 보여도
    # 독립 보존 원칙을 충족했다고 간주하지 않습니다.
    if product_dir.is_symlink() or not product_dir.is_dir():
        reasons.append("final_product_missing")
    else:
        if not product_index.is_file() or product_index.is_symlink():
            reasons.append("final_product_missing")
        if not product_path.is_file() or product_path.is_symlink():
            reasons.append("final_product_missing")
        if not product_assets.is_dir() or product_assets.is_symlink():
            reasons.append("final_product_missing")

    if not reasons:
        try:
            import json

            product = json.loads(product_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            reasons.append("final_product_invalid")
        else:
            if not isinstance(product, dict) or product.get("init") != init or product.get("product_status") not in VALID_PRODUCT_STATUSES:
                reasons.append("final_product_invalid")

    web_dir = web_root / init
    publication_dir = web_dir
    if web_dir.is_symlink():
        # New atomic publishing layout: dated URLs may point to a validated immutable
        # release below web_root/.releases. Only that controlled symlink layout is
        # accepted; arbitrary external symlinks remain invalid.
        try:
            resolved = web_dir.resolve(strict=True)
            release_root = (web_root / ".releases").resolve(strict=True)
            resolved.relative_to(release_root)
        except (OSError, ValueError):
            reasons.append("web_product_missing")
            publication_dir = None
        else:
            publication_dir = resolved
    elif not web_dir.is_dir():
        reasons.append("web_product_missing")
        publication_dir = None

    if publication_dir is not None:
        if not (publication_dir / "index.html").is_file() or (publication_dir / "index.html").is_symlink():
            reasons.append("web_product_missing")
        if not (publication_dir / "forecaster_product.json").is_file() or (publication_dir / "forecaster_product.json").is_symlink():
            reasons.append("web_product_missing")
        if not (publication_dir / "assets").is_dir() or (publication_dir / "assets").is_symlink():
            reasons.append("web_product_missing")
    return list(dict.fromkeys(reasons))


def build_cleanup_plan(
    root: Path,
    *,
    web_root: Path,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    now: datetime | None = None,
    current_init: str | None = None,
) -> list[dict[str, Any]]:
    """Plan only; do not remove any files while resolving retention decisions."""
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
        common_reasons = _publication_reasons(root, web_root, init)
        if now - init_time < timedelta(days=retention_days):
            common_reasons.append("retention_not_expired")
        if init == current_init:
            common_reasons.append("current_init_protected")
        targets: list[dict[str, Any]] = []
        unsafe_runtime_target = False
        for name, base in bases.items():
            if base is None:
                continue
            target = base / init
            if not target.exists() and not target.is_symlink():
                continue
            safe_target = _safe_target(root, base, init)
            if safe_target is None:
                unsafe_runtime_target = True
            targets.append(
                {
                    "target": name,
                    "path": str(target),
                    "safe": safe_target is not None,
                }
            )

        if not targets:
            plan.append({"init": init, "action": "SKIP", "reasons": ["runtime_target_missing"], "targets": []})
            continue

        # 같은 INIT의 dain/daio 중 하나라도 symlink/경로 이탈 등으로 안전하지 않으면
        # 나머지 target도 삭제하지 않습니다. 부분 삭제보다 보존을 우선합니다.
        init_reasons = list(common_reasons)
        if unsafe_runtime_target:
            init_reasons.append("unsafe_target_path")
        init_reasons = list(dict.fromkeys(init_reasons))

        resolved_targets: list[dict[str, Any]] = []
        for target in targets:
            reasons = list(init_reasons)
            resolved_targets.append(
                {
                    "target": target["target"],
                    "path": target["path"],
                    "action": "DELETE" if not reasons else "SKIP",
                    "reasons": reasons,
                }
            )

        plan.append(
            {
                "init": init,
                "action": "DELETE" if all(item["action"] == "DELETE" for item in resolved_targets) else "SKIP",
                "reasons": init_reasons,
                "targets": resolved_targets,
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


def execute_cleanup_plan(root: Path, plan: list[dict[str, Any]], logger: logging.Logger) -> tuple[int, int]:
    """Delete only exact planned dain/daio targets after a second safety check."""
    root = Path(root).resolve()
    deleted = 0
    failures = 0
    for item in plan:
        init = str(item["init"])
        delete_targets = [target for target in item.get("targets", []) if target.get("action") == "DELETE"]
        if not delete_targets:
            continue

        # 계획 수립 이후 파일시스템이 바뀌었을 수 있으므로 한 INIT의 모든 target을
        # 삭제 직전에 다시 검증합니다. 하나라도 안전하지 않으면 해당 INIT은 전부 보존합니다.
        resolved_targets: list[tuple[str, Path]] = []
        unsafe_names: list[str] = []
        for target in delete_targets:
            name = str(target["target"])
            base = _safe_base(root, name)
            safe_target = _safe_target(root, base, init) if base is not None else None
            if safe_target is None:
                unsafe_names.append(name)
            else:
                resolved_targets.append((name, safe_target))
        if unsafe_names:
            logger.error(
                "CLEANUP_FAIL init=%s targets=%s reason=unsafe_target_path; init preserved",
                init,
                ",".join(unsafe_names),
            )
            failures += 1
            continue

        for name, safe_target in resolved_targets:
            try:
                shutil.rmtree(safe_target)
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
    retention_days: int = DEFAULT_RETENTION_DAYS,
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

    deleted, failures = execute_cleanup_plan(root, plan, logger)
    if failures:
        logger.error("CLEANUP_FAIL deleted_targets=%s failed_targets=%s", deleted, failures)
        return plan, 1
    logger.info("CLEANUP_OK dry_run=false delete_candidates=%s skipped=%s deleted_targets=%s", delete_candidates, skipped, deleted)
    return plan, 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely delete expired completed AIFS dain/daio init directories.")
    parser.add_argument("--root", type=Path, default=configured_project_root(PROJECT_ROOT))
    parser.add_argument("--web-root", type=Path, default=Path(os.getenv("AIFS_WEB_ROOT", "/var/www/html")))
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
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
