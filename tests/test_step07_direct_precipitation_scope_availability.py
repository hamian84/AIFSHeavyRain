import unittest

from func.shared.signal_validation import derive_candidate_type, derive_display_tier
from func.step07.checks import _system_direct_precipitation_anchor_context
from func.step08.synthesis import _direct_precipitation_status_contract


CANDIDATE_AREA = {"west": 127.0, "east": 130.0, "south": 39.0, "north": 42.0}


def _context_for_image(image_metadata):
    group = {
        "candidate_id": "CAND_003",
        "candidate_context": {
            "candidate": {
                "candidate_area": CANDIDATE_AREA,
                "peak_forecast_hours": [90],
            }
        },
    }
    check = {"check_id": "CHK_CAND003_07"}
    specification = {
        "intent_contract": {
            "direct_precipitation_anchor": True,
            "source_check_key": "SYS_DIRECT_PRECIPITATION_PRESENCE",
        }
    }
    entries = [{
        "request": {
            "request_id": "REQ_CAND003_PRECIP_F090",
            "requested_domain": CANDIDATE_AREA,
        },
        "step05": {
            "result": {
                "selected_domain": CANDIDATE_AREA,
                "generated_images": [{
                    "physical_diagnostic": "QL_PRECIP_6H",
                    "forecast_hour": 90,
                    **image_metadata,
                }],
            }
        },
    }]
    return _system_direct_precipitation_anchor_context(
        group, check, specification, entries
    )


class DirectPrecipitationScopeAvailabilityTests(unittest.TestCase):
    def test_empty_operational_overlap_is_not_a_threshold_miss(self):
        context = _context_for_image({
            "operational_scope_mask": {
                "selected_region_ids": [],
                "operational_grid_count": 0,
                "status": "empty_operational_overlap",
            },
            "operational_scope_field_value_summary": {
                "finite_count": 0,
                "maximum": None,
            },
            "operational_scope_region_summaries": [],
        })

        self.assertFalse(context["threshold_met"])
        self.assertFalse(context["threshold_evaluation_complete"])
        self.assertEqual(
            context["threshold_evaluation_status"], "unavailable"
        )
        self.assertEqual(
            context["threshold_evaluation_by_forecast_hour"]["F090"]["evaluation_status"],
            "unavailable_no_operational_overlap",
        )

    def test_valid_zero_precipitation_is_a_complete_threshold_miss(self):
        context = _context_for_image({
            "operational_scope_mask": {
                "selected_region_ids": ["LAND_TEST"],
                "operational_grid_count": 4,
                "status": "applied",
            },
            "operational_scope_field_value_summary": {
                "finite_count": 4,
                "maximum": 0.0,
            },
            "operational_scope_region_summaries": [{
                "region_id": "LAND_TEST",
                "region_name": "검증 육상권역",
                "maximum_6h_mm": 0.0,
            }],
        })

        self.assertFalse(context["threshold_met"])
        self.assertTrue(context["threshold_evaluation_complete"])
        self.assertEqual(context["threshold_evaluation_status"], "not_met")
        self.assertEqual(
            context["threshold_evaluation_by_forecast_hour"]["F090"]["evaluation_status"],
            "evaluated",
        )

    def test_missing_precipitation_values_are_unavailable_with_region_overlap(self):
        context = _context_for_image({
            "operational_scope_mask": {
                "selected_region_ids": ["LAND_TEST"],
                "operational_grid_count": 4,
                "status": "applied",
            },
            "operational_scope_field_value_summary": {
                "finite_count": 0,
                "maximum": None,
            },
            "operational_scope_region_summaries": [],
        })

        self.assertFalse(context["threshold_met"])
        self.assertFalse(context["threshold_evaluation_complete"])
        self.assertEqual(
            context["threshold_evaluation_by_forecast_hour"]["F090"]["evaluation_status"],
            "unavailable_precipitation_values",
        )

    def test_missing_scope_metadata_is_not_reported_as_no_overlap(self):
        context = _context_for_image({
            "operational_scope_field_value_summary": {
                "finite_count": 0,
                "maximum": None,
            },
            "operational_scope_region_summaries": [],
        })

        self.assertFalse(context["threshold_evaluation_complete"])
        self.assertEqual(
            context["threshold_evaluation_by_forecast_hour"]["F090"]["evaluation_status"],
            "unavailable_scope_metadata",
        )

    def test_existing_one_mm_presence_rule_remains_evaluable(self):
        context = _context_for_image({
            "operational_scope_mask": {
                "selected_region_ids": ["LAND_TEST"],
                "operational_grid_count": 4,
                "status": "applied",
            },
            "operational_scope_field_value_summary": {
                "finite_count": 4,
                "maximum": 1.2,
            },
            "operational_scope_region_summaries": [{
                "region_id": "LAND_TEST",
                "region_name": "검증 육상권역",
                "maximum_6h_mm": 1.2,
            }],
        })

        self.assertTrue(context["threshold_met"])
        self.assertTrue(context["threshold_evaluation_complete"])
        self.assertEqual(context["threshold_met_forecast_hours"], [90])
        self.assertEqual(
            context["threshold_met_regions_by_forecast_hour"]["F090"][0]["region_id"],
            "LAND_TEST",
        )

    def test_step08_keeps_unavailable_scope_distinct_from_absence(self):
        group = {
            "candidate_id": "CAND_003",
            "candidate_context": {
                "analysis_plan": {
                    "variable_checks": [{
                        "check_id": "CHK_CAND003_07",
                        "intent_contract": {
                            "direct_precipitation_anchor": True,
                            "source_check_key": "SYS_DIRECT_PRECIPITATION_PRESENCE",
                        },
                    }]
                }
            },
        }
        unavailable = _direct_precipitation_status_contract(
            group,
            {"CHK_CAND003_07": {
                "assessment": "insufficient_evidence",
                "evidence_status": "complete",
                "evidence_complete": True,
                "usable_for_synthesis": True,
            }},
        )
        absent = _direct_precipitation_status_contract(
            group,
            {"CHK_CAND003_07": {
                "assessment": "not_observed",
                "evidence_status": "complete",
                "evidence_complete": True,
                "usable_for_synthesis": True,
            }},
        )

        self.assertEqual(unavailable["required_status"], "insufficient_evidence")
        self.assertEqual(absent["required_status"], "not_confirmed")

    def test_step09_retains_precursor_path_without_confirmed_direct_precipitation(self):
        direct_status = "insufficient_evidence"
        candidate_type = derive_candidate_type("precursor_environment", direct_status)
        tier = derive_display_tier({
            "validation_status": "valid",
            "candidate_type": candidate_type,
            "direct_precipitation_status": direct_status,
            "environment_support_status": "confirmed",
            "physical_consistency_status": "coherent",
            "monitoring_value": "meaningful",
            "independent_signal_categories": ["moisture_supply", "low_level_forcing"],
            "spatiotemporal_alignment": True,
            "evidence_completeness": {"sufficient": True},
        })

        self.assertEqual(candidate_type, "precursor_environment")
        self.assertEqual(tier, "elevated")


if __name__ == "__main__":
    unittest.main()
