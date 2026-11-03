import unittest

from func.step04.pass1_engine import (
    ResponseValidationError,
    call_and_validate_json,
    _compact_candidate_chunk_repair_required,
    _repair_rules_for_validation_errors,
    _validate_compact_quantitative_atomic_claims,
)
from func.step07.checks import _single_majority_convergence_not_strengthen_claim


def _check(quantitative_scope=None):
    return {
        "check_key": "chunk01:CHK04",
        "analysis_methods": ["temporal_change"],
        "atomic_claims": [
            {
                "claim_key": "CLM01",
                "claim_statement": "대부분 권역에서 925 hPa 수렴이 강화되지 않는다.",
                "expected_state": "not_strengthen",
                "scope_quantifier": "majority",
                "phase_keys": ["PHASE01"],
                "quantitative_scope": quantitative_scope,
            }
        ],
    }


class UnavailableMajorityScopeTests(unittest.TestCase):
    def _validate(self, check, selectable_regions):
        with self.assertRaisesRegex(ValueError, "CANDIDATE_QUANTITATIVE_SCOPE_UNAVAILABLE") as caught:
            _validate_compact_quantitative_atomic_claims(
                check,
                support_catalog={},
                candidate={},
                selectable_operational_regions=selectable_regions,
                referenced_hours={78, 84, 90, 96},
                phase_hours_by_key={"PHASE01": {78, 84, 90, 96}},
            )
        return str(caught.exception)

    def test_majority_without_operational_overlap_is_a_chunk_level_scope_error(self):
        error = self._validate(_check(), [])
        self.assertIn("repair_unit=candidate_plan_chunk", error)
        self.assertIn("check_key=chunk01:CHK04", error)
        self.assertIn("claim_key=CLM01", error)

    def test_valid_majority_contract_is_still_rejected_without_step05_regions(self):
        scope = {
            "feature_id": "convergence925_1e5_s",
            "region_scope": "candidate_overlap",
            "region_ids": [],
            "forecast_hours": [78, 84],
            "comparison_forecast_hours": None,
            "statistic": "mean",
        }
        error = self._validate(_check(scope), [])
        self.assertIn("overlaps no Step05-eligible", error)

    def test_overlapping_candidate_keeps_existing_missing_contract_error(self):
        check = _check()
        with self.assertRaisesRegex(
            ValueError,
            "majority not_strengthen requires a two-hour 925 hPa convergence quantitative_scope",
        ):
            _validate_compact_quantitative_atomic_claims(
                check,
                support_catalog={},
                candidate={},
                selectable_operational_regions=[{"region_id": "LAND_EXAMPLE"}],
                referenced_hours={78, 84},
                phase_hours_by_key={"PHASE01": {78, 84}},
            )

    def test_only_candidate_level_scope_error_escalates_above_check(self):
        structural = ["CANDIDATE_QUANTITATIVE_SCOPE_UNAVAILABLE repair_unit=candidate_plan_chunk"]
        independent = ["QUANTITATIVE_SCOPE_CONTRACT_INVALID check_key=chunk01:CHK04"]
        self.assertTrue(_compact_candidate_chunk_repair_required(structural))
        self.assertFalse(_compact_candidate_chunk_repair_required(independent))

    def test_scope_repair_guidance_preserves_candidate_and_quantitative_meaning(self):
        rules = "\n".join(_repair_rules_for_validation_errors([
            "CANDIDATE_QUANTITATIVE_SCOPE_UNAVAILABLE"
        ]))
        self.assertIn("원래 후보 가설의 기상학적 목적은 유지", rules)
        self.assertIn("정성 판독으로 바꾸거나", rules)
        self.assertIn("candidate_area를 확대·이동", rules)

    def test_valid_majority_contract_remains_recognized_by_step07(self):
        claim = _check({
            "feature_id": "convergence925_1e5_s",
            "region_scope": "candidate_overlap",
            "region_ids": [],
            "forecast_hours": [78, 84],
            "comparison_forecast_hours": None,
            "statistic": "mean",
        })["atomic_claims"][0]
        specification = {
            "atomic_claim_contract": {"canonical_claims": [claim]}
        }
        self.assertEqual(
            _single_majority_convergence_not_strengthen_claim(specification), claim
        )

    def test_overlapping_candidate_accepts_supported_two_hour_majority_contract(self):
        check = _check({
            "feature_id": "convergence925_1e5_s",
            "region_scope": "candidate_overlap",
            "region_ids": [],
            "forecast_hours": [78, 84],
            "comparison_forecast_hours": None,
            "statistic": "mean",
        })
        check.update({
            "diagnostic_id": "horizontal_convergence",
            "variable_role": "low_level_forcing",
            "required_signal_components": ["low_level_forcing"],
            "analysis_methods": ["temporal_evolution"],
            "phase_keys": ["PHASE01"],
        })
        support = {
            "spatial_field_products": [{"product_id": "QL_CONVERGENCE925_WIND925"}],
            "diagnostic_execution_support": {
                "horizontal_convergence": {
                    "compatible_product_ids": ["QL_CONVERGENCE925_WIND925"]
                }
            },
            "quantitative_excerpt_support": {
                "feature_ids_by_product": {
                    "QL_CONVERGENCE925_WIND925": ["convergence925_1e5_s"]
                }
            },
        }
        _validate_compact_quantitative_atomic_claims(
            check,
            support_catalog=support,
            candidate={"related_product_ids": ["QL_CONVERGENCE925_WIND925"]},
            selectable_operational_regions=[{"region_id": "LAND_EXAMPLE"}],
            referenced_hours={78, 84},
            phase_hours_by_key={"PHASE01": {78, 84}},
        )

    def test_unavailable_scope_uses_candidate_chunk_repair_without_check_repair(self):
        class Result:
            response_envelope = {}
            usage_attempts = []
            http_retry_count = 0
            structured_output_used = True
            provider_extensions_used = False

            def __init__(self, content):
                self.content = content

        class Client:
            def __init__(self):
                self.calls = []

            def complete(self, **kwargs):
                self.calls.append(kwargs)
                return Result('{"analysis_phases":[],"diagnostic_checks":[]}')

        client = Client()
        validations = iter([
            ["CANDIDATE_QUANTITATIVE_SCOPE_UNAVAILABLE check_key=chunk01:CHK04"],
            [],
        ])
        call_and_validate_json(
            client=client,
            model="test-model",
            messages=[{"role": "user", "content": "original candidate context"}],
            schema={"type": "object"},
            schema_name="CandidatePlan",
            structured_output_mode="json_schema",
            enable_thinking=False,
            max_tokens=1000,
            temperature=0.0,
            top_p=1.0,
            repair_attempts=1,
            semantic_validator=lambda _value: next(validations),
            repair_context_messages=[{"role": "user", "content": "original candidate context"}],
            semantic_unit_repair="diagnostic_check",
            chunk_repair_from_original_context=True,
        )

        self.assertEqual([call["schema_name"] for call in client.calls], [
            "CandidatePlan", "CandidatePlan"
        ])
        self.assertIn("candidate-plan chunk", client.calls[1]["messages"][-1]["content"])
        self.assertIn("CANDIDATE_QUANTITATIVE_SCOPE_UNAVAILABLE", client.calls[1]["messages"][-1]["content"])

    def test_independent_check_error_still_uses_check_repair(self):
        class Result:
            response_envelope = {}
            usage_attempts = []
            http_retry_count = 0
            structured_output_used = True
            provider_extensions_used = False

            def __init__(self, content):
                self.content = content

        class Client:
            def __init__(self):
                self.calls = []

            def complete(self, **kwargs):
                self.calls.append(kwargs)
                content = (
                    '{"analysis_phases":[],"diagnostic_checks":[{"check_key":"chunk01:CHK04","value":"old"}]}'
                    if len(self.calls) == 1
                    else '{"diagnostic_checks":[{"check_key":"chunk01:CHK04","value":"rebuilt"}]}'
                )
                return Result(content)

        client = Client()
        schema = {
            "type": "object",
            "properties": {
                "diagnostic_checks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "check_key": {"type": "string"},
                            "value": {"type": "string"},
                        },
                        "required": ["check_key", "value"],
                    },
                }
            },
            "required": ["diagnostic_checks"],
        }
        validations = iter([
            ["INDEPENDENT_CHECK_INVALID check_key=chunk01:CHK04"],
            [],
        ])
        call_and_validate_json(
            client=client,
            model="test-model",
            messages=[{"role": "user", "content": "candidate context"}],
            schema=schema,
            schema_name="CandidatePlan",
            structured_output_mode="json_schema",
            enable_thinking=False,
            max_tokens=1000,
            temperature=0.0,
            top_p=1.0,
            repair_attempts=1,
            semantic_validator=lambda _value: next(validations),
            repair_context_messages=[{"role": "user", "content": "candidate context"}],
            semantic_unit_repair="diagnostic_check",
        )
        self.assertEqual(client.calls[1]["schema_name"], "CandidatePlanDiagnosticCheckRepair")
        self.assertEqual(len(client.calls), 2)

    def test_repeated_structural_error_stops_at_chunk_budget(self):
        class Result:
            response_envelope = {}
            usage_attempts = []
            http_retry_count = 0
            structured_output_used = True
            provider_extensions_used = False
            content = '{"analysis_phases":[],"diagnostic_checks":[]}'

        class Client:
            def __init__(self):
                self.calls = 0

            def complete(self, **_kwargs):
                self.calls += 1
                return Result()

        client = Client()
        error = "CANDIDATE_QUANTITATIVE_SCOPE_UNAVAILABLE check_key=chunk01:CHK04"
        with self.assertRaisesRegex(
            ResponseValidationError, "STEP04_CANDIDATE_CHUNK_REPAIR_NONPROGRESS"
        ) as caught:
            call_and_validate_json(
                client=client,
                model="test-model",
                messages=[{"role": "user", "content": "original candidate context"}],
                schema={"type": "object"},
                schema_name="CandidatePlan",
                structured_output_mode="json_schema",
                enable_thinking=False,
                max_tokens=1000,
                temperature=0.0,
                top_p=1.0,
                repair_attempts=1,
                semantic_validator=lambda _value: [error],
                repair_context_messages=[{"role": "user", "content": "original candidate context"}],
                semantic_unit_repair="diagnostic_check",
                chunk_repair_from_original_context=True,
            )
        self.assertEqual(client.calls, 2)
        self.assertEqual(caught.exception.diagnostics["candidate_chunk_repair_attempt_count"], 1)


if __name__ == "__main__":
    unittest.main()
