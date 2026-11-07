"""Read-only cross-step step08 public contract from split modules.
No dependency on the retired combined module.
"""
from __future__ import annotations
from . import synthesis_analysis as analysis
from . import synthesis_validation as validation
from . import synthesis_workflow as workflow
from . import synthesis_support as support
from . import synthesis_context as context
from .synthesis_runtime import configure
configure(analysis=analysis, validation=validation, workflow=workflow, support=support)

_candidate_analysis_region_index = support._candidate_analysis_region_index
_candidate_name_grounding_audit = validation._candidate_name_grounding_audit
_candidate_name_grounding_contract = validation._candidate_name_grounding_contract
_derive_environment_localization = support._derive_environment_localization
_derive_final_spatial_identity = workflow._derive_final_spatial_identity
_provenance = workflow._provenance
_step08_alignment_narrative_errors = validation._step08_alignment_narrative_errors
_verified_quantitative_relations = validation._verified_quantitative_relations
migrate_step08_document = workflow.migrate_step08_document
step08_output_path = workflow.step08_output_path
validate_candidate_synthesis = validation.validate_candidate_synthesis
validate_operational_brief = validation.validate_operational_brief
validate_operational_guidance = validation.validate_operational_guidance
