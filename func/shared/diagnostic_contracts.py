"""Deterministic diagnostic/product signal-component contracts.

``daba/analysis/diagnostic_catalog.yaml`` is the single source of truth for
what a Step05 product *can* provide.  This module contains only the small
runtime helpers needed to read that contract and to resolve what a particular
Step05 result actually produced.  No meteorological outcome is inferred here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml

from func.shared.runtime_paths import runtime_path


SIGNAL_COMPONENTS = frozenset({
    "direct_precipitation",
    "moisture_supply",
    "low_level_forcing",
    "mid_upper_forcing",
    "thermodynamic_support",
    "organization_persistence",
    "synoptic_support",
    "uncertainty",
})


@lru_cache(maxsize=8)
def _load_catalog_cached(path_text: str, mtime_ns: int) -> dict[str, Any]:
    path = Path(path_text)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"diagnostic catalog root must be an object: {path}")
    return data


def load_diagnostic_catalog(root: Path) -> dict[str, Any]:
    path = runtime_path(root, "daba", "analysis", "diagnostic_catalog.yaml")
    stat = path.stat()
    return _load_catalog_cached(str(path), stat.st_mtime_ns)


def _normalize_components(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        component = str(value)
        if component not in SIGNAL_COMPONENTS:
            raise ValueError(f"unsupported signal component in diagnostic catalog: {component!r}")
        if component not in result:
            result.append(component)
    return result


def product_contract(catalog: dict[str, Any], product_id: str) -> dict[str, Any]:
    contracts = catalog.get("product_contracts")
    if not isinstance(contracts, dict):
        return {}
    value = contracts.get(str(product_id))
    return value if isinstance(value, dict) else {}


def product_signal_components(catalog: dict[str, Any], product_id: str) -> list[str]:
    """Return the catalog-declared capability for one product."""
    return _normalize_components(product_contract(catalog, product_id).get("signal_components"))


def product_signal_component_requirements(
    catalog: dict[str, Any], product_id: str,
) -> dict[str, dict[str, Any]]:
    contract = product_contract(catalog, product_id)
    raw = contract.get("signal_component_requirements")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for component, requirement in raw.items():
        component = str(component)
        if component not in SIGNAL_COMPONENTS:
            raise ValueError(f"unsupported signal component requirement: {component!r}")
        if not isinstance(requirement, dict):
            raise ValueError(f"signal component requirement must be an object: {product_id}/{component}")
        result[component] = requirement
    return result


def _string_set(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values if str(value)}


def actual_signal_components(
    catalog: dict[str, Any],
    *,
    product_id: str,
    generated_images: Iterable[dict[str, Any]] | None,
    features: dict[str, Any] | None,
) -> list[str]:
    """Resolve signal components actually present in one Step05 result.

    The catalog declares capability; the generated image metadata and feature
    IDs declare what exists in this execution.  Optional fields therefore do
    not become available merely because a product can theoretically provide
    them.
    """
    capabilities = product_signal_components(catalog, product_id)
    if not capabilities:
        return []
    image_diagnostics = {
        str(item.get("physical_diagnostic"))
        for item in (generated_images or [])
        if isinstance(item, dict) and str(item.get("physical_diagnostic") or "")
    }
    feature_ids = {
        str(key)
        for key, value in (features or {}).items()
        if str(key) and isinstance(value, dict)
    }
    has_any_artifact = bool(image_diagnostics or feature_ids)
    requirements = product_signal_component_requirements(catalog, product_id)
    available: list[str] = []
    for component in capabilities:
        requirement = requirements.get(component)
        if requirement is None:
            if has_any_artifact:
                available.append(component)
            continue
        image_any = _string_set(requirement.get("any_physical_diagnostics"))
        feature_any = _string_set(requirement.get("any_feature_ids"))
        image_all = _string_set(requirement.get("all_physical_diagnostics"))
        feature_all = _string_set(requirement.get("all_feature_ids"))
        predicates: list[bool] = []
        if image_any:
            predicates.append(bool(image_diagnostics & image_any))
        if feature_any:
            predicates.append(bool(feature_ids & feature_any))
        if image_all:
            predicates.append(image_all.issubset(image_diagnostics))
        if feature_all:
            predicates.append(feature_all.issubset(feature_ids))
        # any_* requirements are alternatives, while all_* are mandatory.
        any_predicates = []
        if image_any:
            any_predicates.append(bool(image_diagnostics & image_any))
        if feature_any:
            any_predicates.append(bool(feature_ids & feature_any))
        any_ok = True if not any_predicates else any(any_predicates)
        all_ok = True
        if image_all:
            all_ok = all_ok and image_all.issubset(image_diagnostics)
        if feature_all:
            all_ok = all_ok and feature_all.issubset(feature_ids)
        if any_ok and all_ok and (any_predicates or image_all or feature_all):
            available.append(component)
    return available


def capability_contract_version(catalog: dict[str, Any]) -> str:
    """Return the semantic capability-contract version declared by the catalog."""
    value = str(catalog.get("capability_contract_version") or "").strip()
    return value or "legacy-unversioned"


def diagnostic_semantic_capability(
    catalog: dict[str, Any],
    *,
    diagnostic_id: str,
    product_id: str,
) -> dict[str, Any]:
    """Resolve the observable-level execution capability for one diagnostic/product pair.

    This is a deterministic execution contract.  It describes what Step05 can
    actually expose; it does not infer whether a meteorological signal is present.
    """
    compatibility = catalog.get("compatibility")
    if not isinstance(compatibility, dict):
        return {}
    diagnostic = compatibility.get(str(diagnostic_id))
    if not isinstance(diagnostic, dict):
        return {}
    profiles = diagnostic.get("semantic_profiles")
    if not isinstance(profiles, dict):
        return {}
    profile = profiles.get(str(product_id))
    if not isinstance(profile, dict):
        return {}
    result = {
        "contract_version": capability_contract_version(catalog),
        "diagnostic_id": str(diagnostic_id),
        "source_product_id": str(product_id),
        "execution_product_id": str(profile.get("execution_product_id") or product_id),
        "supported_verification_modes": [
            str(value)
            for value in diagnostic.get("supported_verification_modes", [])
            if str(value)
        ],
        "signal_components": _normalize_components(profile.get("signal_components")),
        "observable_ids": [
            str(value)
            for value in profile.get("observable_ids", [])
            if str(value)
        ],
    }
    field_product_id = str(profile.get("field_product_id") or "").strip()
    if field_product_id:
        result["field_product_id"] = field_product_id
    quantitative_feature_ids = [
        str(value)
        for value in profile.get("quantitative_feature_ids", [])
        if str(value)
    ]
    if quantitative_feature_ids:
        result["quantitative_feature_ids"] = list(dict.fromkeys(quantitative_feature_ids))
    return result


def validate_semantic_capability_catalog(catalog: dict[str, Any]) -> None:
    """Validate the configuration shape of the Step05 semantic capability contract.

    This validates only declared execution capabilities. It does not judge whether
    a meteorological signal is present or whether an expected_signal is true.
    """
    diagnostics = catalog.get("diagnostics")
    compatibility = catalog.get("compatibility")
    if not isinstance(diagnostics, list) or not isinstance(compatibility, dict):
        raise ValueError("diagnostic catalog lacks diagnostics/compatibility for semantic capability validation")
    operational_ids = {
        str(item.get("diagnostic_id"))
        for item in diagnostics
        if isinstance(item, dict)
        and str(item.get("maturity", "operational")) == "operational"
        and item.get("diagnostic_id")
    }
    for diagnostic_id in sorted(operational_ids):
        contract = compatibility.get(diagnostic_id)
        if not isinstance(contract, dict):
            raise ValueError(f"operational diagnostic lacks compatibility contract: {diagnostic_id}")
        modes = contract.get("supported_verification_modes")
        profiles = contract.get("semantic_profiles")
        if not isinstance(modes, list) or not modes:
            raise ValueError(f"semantic capability modes missing: {diagnostic_id}")
        if not isinstance(profiles, dict) or not profiles:
            raise ValueError(f"semantic capability profiles missing: {diagnostic_id}")
        compatible_products = {str(value) for value in contract.get("compatible_product_ids", []) if str(value)}
        for source_product_id, profile in profiles.items():
            if not isinstance(profile, dict):
                raise ValueError(f"semantic capability profile must be object: {diagnostic_id}/{source_product_id}")
            if compatible_products and str(source_product_id) not in compatible_products:
                raise ValueError(
                    f"semantic capability source product is not compatible: {diagnostic_id}/{source_product_id}"
                )
            execution_product_id = str(profile.get("execution_product_id") or "").strip()
            if not execution_product_id:
                raise ValueError(f"semantic capability execution product missing: {diagnostic_id}/{source_product_id}")
            components = _normalize_components(profile.get("signal_components"))
            if not components:
                raise ValueError(f"semantic capability signal components missing: {diagnostic_id}/{source_product_id}")
            observables = [str(value) for value in profile.get("observable_ids", []) if str(value)]
            if not observables:
                raise ValueError(f"semantic capability observables missing: {diagnostic_id}/{source_product_id}")
            field_product_id = profile.get("field_product_id")
            if field_product_id is not None and not str(field_product_id).strip():
                raise ValueError(f"semantic capability field_product_id is blank: {diagnostic_id}/{source_product_id}")
            quantitative_feature_ids = profile.get("quantitative_feature_ids")
            if quantitative_feature_ids is not None:
                if (
                    not isinstance(quantitative_feature_ids, list)
                    or not quantitative_feature_ids
                    or not all(str(value).strip() for value in quantitative_feature_ids)
                ):
                    raise ValueError(
                        f"semantic capability quantitative_feature_ids invalid: {diagnostic_id}/{source_product_id}"
                    )


def semantic_capability_registry(catalog: dict[str, Any]) -> dict[str, Any]:
    """Build a compact, serializable capability registry for Step03/Step04 lineage."""
    validate_semantic_capability_catalog(catalog)
    compatibility = catalog.get("compatibility")
    diagnostics: dict[str, Any] = {}
    if isinstance(compatibility, dict):
        for diagnostic_id, contract in compatibility.items():
            if not isinstance(contract, dict):
                continue
            profiles = contract.get("semantic_profiles")
            if not isinstance(profiles, dict) or not profiles:
                continue
            diagnostics[str(diagnostic_id)] = {
                "supported_verification_modes": [
                    str(value)
                    for value in contract.get("supported_verification_modes", [])
                    if str(value)
                ],
                "profiles": {
                    str(product_id): diagnostic_semantic_capability(
                        catalog,
                        diagnostic_id=str(diagnostic_id),
                        product_id=str(product_id),
                    )
                    for product_id in profiles
                },
            }
    return {
        "contract_version": capability_contract_version(catalog),
        "diagnostics": diagnostics,
    }
