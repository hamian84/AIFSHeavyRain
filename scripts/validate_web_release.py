"""Validate a fully staged AIFSHeavyRain web release before link activation."""

from __future__ import annotations

import argparse
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
import sys

# Pipeline invokes this script by its absolute path; expose the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from func.shared.majority_narrative import (  # noqa: E402
    MajorityNarrativeError, majority_statements, validate_majority_product,
)
from func.step10.forecaster_product import _render_text  # noqa: E402
from func.shared.tier_narrative import TierNarrativeError, assert_public_tier  # noqa: E402
from urllib.parse import parse_qs, urlsplit


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class _ImageReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[str] = []
        self.related_selections: list[str] = []
        self.majority_digests: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value for key, value in attrs if value is not None}
        if "majority-aggregation" in (values.get("class") or "").split():
            digest = values.get("data-majority-sha256")
            if not digest:
                raise ValueError("majority aggregation HTML is missing integrity marker")
            self.majority_digests.append(digest)
        if tag == "img":
            for key in ("src", "data-src"):
                value = values.get(key)
                if value:
                    self.refs.append(value)
        value = values.get("data-image-path")
        if value:
            self.refs.append(value)
        related = values.get("data-related-image")
        if related:
            try:
                payload = json.loads(related)
            except json.JSONDecodeError:
                return
            if isinstance(payload, dict) and isinstance(payload.get("relative_path"), str):
                self.refs.append(payload["relative_path"])
                if isinstance(payload.get("selection"), str):
                    self.related_selections.append(payload["selection"])
        series = values.get("data-quant-series")
        if series:
            try:
                payload = json.loads(series)
            except json.JSONDecodeError:
                return
            if not isinstance(payload, dict):
                return
            for region in payload.get("regions", []):
                if not isinstance(region, dict):
                    continue
                for point in region.get("points", []):
                    if not isinstance(point, dict):
                        continue
                    related_image = point.get("related_image")
                    if isinstance(related_image, dict) and isinstance(related_image.get("relative_path"), str):
                        self.refs.append(related_image["relative_path"])
                        if isinstance(related_image.get("selection"), str):
                            self.related_selections.append(related_image["selection"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_image_path(release_dir: Path, ref: str) -> tuple[Path, str | None]:
    parsed = urlsplit(ref)
    if parsed.scheme or parsed.netloc or parsed.path.startswith("/"):
        raise ValueError(f"non-local image reference is not allowed: {ref}")
    relative = Path(parsed.path)
    if any(part in {"..", ""} for part in relative.parts):
        raise ValueError(f"unsafe image reference: {ref}")
    path = release_dir / relative
    try:
        path.resolve().relative_to(release_dir.resolve())
    except (OSError, ValueError) as exc:
        raise ValueError(f"image reference escapes release directory: {ref}") from exc
    version = parse_qs(parsed.query).get("v", [None])[0]
    return path, version


def validate_release(release_dir: Path) -> dict[str, int]:
    release_dir = release_dir.resolve()
    index_path = release_dir / "index.html"
    product_path = release_dir / "forecaster_product.json"
    asset_dir = release_dir / "assets"
    if not index_path.is_file() or not product_path.is_file() or not asset_dir.is_dir():
        raise ValueError("release requires index.html, forecaster_product.json, and assets/")

    product = json.loads(product_path.read_text(encoding="utf-8"))
    if not isinstance(product, dict):
        raise ValueError("forecaster_product.json must contain an object")

    try:
        expected_majority_digests = validate_majority_product(product)
    except MajorityNarrativeError as exc:
        raise ValueError(f"QUANTITATIVE_NARRATIVE_INVALID: {exc}") from exc
    html = index_path.read_text(encoding="utf-8")
    parser = _ImageReferenceParser()
    parser.feed(html)
    if sorted(expected_majority_digests) != sorted(parser.majority_digests):
        raise ValueError("HTML/JSON majority aggregation facts or number of Checks disagree")
    # An answer is already checked against Step07 before the Step10 product
    # is rendered. Ensure the staged HTML still contains that exact approved
    # answer: do not allow a separately edited HTML/JSON pair to diverge.
    for candidate in product.get("surfaced_candidates", []):
        for question in candidate.get("forecaster_question_answers", []):
            rendered = '<p class="question-answer">' + _render_text(question["answer"], product["init"]) + '</p>'
            if rendered not in html:
                raise ValueError(
                    "FORECASTER_QUESTION_PUBLICATION_MISMATCH: staged HTML does not contain approved answer "
                    + str(question.get("forecaster_question_id") or "")
                )
    # A staged product must not reintroduce an LLM tier conclusion that
    # contradicts Step09, and the visible justification must be verbatim.
    for candidate in product.get("surfaced_candidates", []):
        try:
            assert_public_tier(candidate)
        except TierNarrativeError as exc:
            raise ValueError(f"TIER_NARRATIVE_INVALID: {exc}") from exc
        reason = candidate.get("operational_surface_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("TIER_NARRATIVE_INVALID: missing public reason")
        rendered = '<p class="internal-judgment-reason"><strong>감시 판단 이유</strong> ' + _render_text(reason, product["init"]) + '</p>'
        if rendered not in html:
            raise ValueError("TIER_NARRATIVE_PUBLICATION_MISMATCH: approved tier reason is absent from HTML")
    # Verify the authoritative confirmed/exception sentences, not just the
    # summary table, are rendered. Step10 converts F-hour tokens into local
    # datetimes, so compare the invariant quantified clauses.
    from html import escape as html_escape
    for candidate in product.get("surfaced_candidates", []):
        for block in candidate.get("evidence_blocks", []):
            aggregation = block.get("majority_aggregation")
            if not isinstance(aggregation, dict):
                continue
            confirmed, exception = majority_statements(aggregation)
            clause = confirmed.split("까지 ", 1)[-1]
            if html_escape(clause) not in html:
                raise ValueError("majority confirmed facts missing from HTML")
            if aggregation["strengthening_count"] and html_escape(exception) not in html:
                raise ValueError("majority strengthening exceptions missing from HTML")
    unique_refs = list(dict.fromkeys(parser.refs))
    invalid_selections = sorted({value for value in parser.related_selections if value != "same_time_related"})
    if invalid_selections:
        raise ValueError(
            "quantitative image links must use exact-time related images only: "
            + ",".join(invalid_selections)
        )
    if product.get("surfaced_candidates") and not unique_refs:
        raise ValueError("published surfaced candidates have no HTML image references")

    checked_paths: dict[Path, str] = {}
    for ref in unique_refs:
        path, version = _local_image_path(release_dir, ref)
        if not path.is_file():
            raise ValueError(f"referenced image is missing: {ref}")
        with path.open("rb") as handle:
            if handle.read(8) != PNG_MAGIC:
                raise ValueError(f"referenced image is not a valid PNG: {ref}")
        digest = checked_paths.get(path)
        if digest is None:
            digest = _sha256(path)
            checked_paths[path] = digest
        if version is None:
            raise ValueError(f"referenced image has no cache-busting version: {ref}")
        if version.lower() != digest[: len(version)].lower():
            raise ValueError(f"image version does not match content hash: {ref}")

    assets = product.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("forecaster_product assets must be an array")
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("forecaster_product asset must be an object")
        relative = asset.get("relative_path")
        declared = asset.get("sha256")
        if not isinstance(relative, str) or not isinstance(declared, str):
            raise ValueError("forecaster_product asset path/hash is invalid")
        path, _ = _local_image_path(release_dir, relative)
        if not path.is_file():
            raise ValueError(f"published product asset is missing: {relative}")
        digest = checked_paths.get(path) or _sha256(path)
        checked_paths[path] = digest
        if digest.lower() != declared.lower():
            raise ValueError(f"published product asset hash mismatch: {relative}")

    return {
        "html_image_references": len(unique_refs),
        "unique_image_files": len(checked_paths),
        "product_assets": len(assets),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a staged AIFSHeavyRain web release.")
    parser.add_argument("--release-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = validate_release(args.release_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"WEB_RELEASE_VALIDATION_FAIL error={exc}")
        return 2
    print(
        "WEB_RELEASE_VALIDATION_OK"
        f" html_image_references={result['html_image_references']}"
        f" unique_image_files={result['unique_image_files']}"
        f" product_assets={result['product_assets']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
