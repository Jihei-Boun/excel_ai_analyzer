"""다중 파일 구조화 통합 파이프라인.

Phase 14+: Cross-file Data Understanding
Phase 15+: IntegrationPlan v1 Planner
Phase 16+: IntegrationPlan Validator
Phase 17+: Deterministic Integration Executor
Phase 18+: Result Validator + Planner recovery loop
  integration_result_validation_types / integration_result_validate / integration_pipeline
Legacy integrate path (aggregate_merge) remains in plan_* modules.
route_multi is NOT switched in Phase 18.
"""

from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_execution_types import (
    IntegrationExecutionError,
    IntegrationExecutionResult,
    IntegrationStepExecutionResult,
)
from core.integrate.integration_pipeline import (
    IntegrationPipelineResult,
    run_integration_pipeline,
)
from core.integrate.integration_plan_types import (
    IntegrationPlan,
    IntegrationPlanParseError,
    integration_plan_from_dict,
)
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.integration_planner import build_integration_plan
from core.integrate.integration_result_validate import validate_integration_result
from core.integrate.integration_result_validation_types import (
    IntegrationResultValidationIssue,
    IntegrationResultValidationResult,
    format_integration_execution_feedback,
    format_integration_result_validation_feedback,
)
from core.integrate.integration_validation_types import (
    IntegrationValidationIssue,
    IntegrationValidationResult,
    format_integration_validation_feedback,
)
from core.integrate.relationship_infer import (
    build_cross_file_understanding,
    infer_cross_file_relationship,
)
from core.integrate.relationship_profile import (
    build_all_pairwise_observations,
    build_file_profile,
    build_pairwise_observation,
)

__all__ = [
    "build_cross_file_understanding",
    "infer_cross_file_relationship",
    "build_file_profile",
    "build_pairwise_observation",
    "build_all_pairwise_observations",
    "IntegrationPlan",
    "IntegrationPlanParseError",
    "integration_plan_from_dict",
    "build_integration_plan",
    "validate_integration_plan",
    "IntegrationValidationIssue",
    "IntegrationValidationResult",
    "format_integration_validation_feedback",
    "execute_integration_plan",
    "IntegrationExecutionError",
    "IntegrationExecutionResult",
    "IntegrationStepExecutionResult",
    "validate_integration_result",
    "IntegrationResultValidationIssue",
    "IntegrationResultValidationResult",
    "format_integration_result_validation_feedback",
    "format_integration_execution_feedback",
    "run_integration_pipeline",
    "IntegrationPipelineResult",
]
