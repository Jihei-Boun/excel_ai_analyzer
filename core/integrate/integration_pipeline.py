"""Phase 18: Integration pipeline with Result Validator + Planner recovery loop.

Does NOT switch route_multi. No legacy/PandasAI fallback.
max_retries default matches single-file analysis_pipeline (2 → 3 attempts).

Phase 20: also track integration operation family for retry diversity
(observability / feedback only — never synthesizes plans).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_execution_types import IntegrationExecutionResult
from core.integrate.integration_plan_types import (
    IntegrationPlan,
    canonical_integration_plan_signature,
    integration_operation_family_signature,
    repeated_integration_family_feedback,
)
from core.integrate.integration_contracts import (
    FAILURE_TYPE_ALIAS,
    FAILURE_TYPE_STRUCTURAL,
    classify_integration_failure_codes,
    retry_mode_for_failure_type,
)
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.integration_planner import build_integration_plan
from core.integrate.integration_result_validate import validate_integration_result
from core.integrate.integration_result_validation_types import (
    FAILURE_STAGE_EXECUTION,
    FAILURE_STAGE_PLAN_GENERATION,
    FAILURE_STAGE_PLAN_VALIDATION,
    FAILURE_STAGE_RESULT_VALIDATION,
    IntegrationResultValidationResult,
    format_integration_execution_feedback,
    format_integration_result_validation_feedback,
)
from core.integrate.integration_validation_types import (
    IntegrationValidationResult,
    format_integration_validation_feedback,
)
from core.integrate.relationship_types import CrossFileUnderstanding

# Align with single-file analysis_pipeline.max_retries=2 (total attempts = 3)
DEFAULT_MAX_RETRIES = 2

# Execution errors that are plan-dependent (retry candidate)
_RETRYABLE_EXEC_CODES = frozenset(
    {
        "missing_column",
        "missing_dataset",
        "malformed_params",
        "unsupported_operation",
        "unsupported_filter_operator",
        "unsupported_aggregation",
        "union_empty_intersection",
        "aggregate_alias_collision",
        "runtime_error",
    }
)

_UNSAFE_PLAN_CODES = frozenset(
    {
        "ambiguous_key_selection",
        "insufficient_evidence_forced_join",
        "join_against_unrelated",
        "many_to_many_join_risk",
        "extreme_row_amplification",
    }
)


@dataclass
class IntegrationPipelineResult:
    status: str  # success | cannot_plan | failed
    plan: IntegrationPlan | None = None
    plan_validation: IntegrationValidationResult | None = None
    execution: IntegrationExecutionResult | None = None
    result_validation: IntegrationResultValidationResult | None = None
    retry_log: list[dict[str, Any]] = field(default_factory=list)
    final_output: pd.DataFrame | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_dataframe: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "plan": self.plan.to_dict() if self.plan else None,
            "plan_validation": self.plan_validation.to_dict() if self.plan_validation else None,
            "execution": self.execution.to_dict() if self.execution else None,
            "result_validation": (
                self.result_validation.to_dict() if self.result_validation else None
            ),
            "retry_log": list(self.retry_log),
            "metadata": dict(self.metadata),
        }
        if self.final_output is not None:
            payload["final_shape"] = [
                int(self.final_output.shape[0]),
                int(self.final_output.shape[1]),
            ]
            payload["final_columns"] = [str(c) for c in self.final_output.columns]
            if include_dataframe:
                payload["final_output"] = self.final_output.to_dict(orient="list")
        return payload


def run_integration_pipeline(
    user_prompt: str,
    sources: dict[str, pd.DataFrame],
    understanding: CrossFileUnderstanding | dict[str, Any],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_url: str = "http://localhost:11434",
    model: str = "qwen2.5:7b",
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
    build_plan_fn: Callable[..., IntegrationPlan] | None = None,
) -> IntegrationPipelineResult:
    """Plan → validate → execute → result-validate with limited Planner retries.

    No route_multi switch. No legacy fallback. cannot_plan is a safe outcome.
    """
    planner = build_plan_fn or build_integration_plan
    retry_log: list[dict[str, Any]] = []
    feedback: list[str] = []
    seen_signatures: set[str] = set()
    rejected_families: list[str] = []
    duplicate_plan_count = 0
    same_family_repeat_count = 0
    plan_validation_failure_count = 0
    execution_failure_count = 0
    result_validation_failure_count = 0
    validator_blocked_unsafe_plan = False
    first_plan_ops: list[str] = []
    first_plan_family: str | None = None
    first_plan_success = False
    repeated_contract_failure = False
    last_structural_codes: tuple[str, ...] | None = None
    expected_schema_by_step: list[dict[str, Any]] = []
    actual_schema_by_step: list[dict[str, Any]] = []

    last_plan: IntegrationPlan | None = None
    last_plan_val: IntegrationValidationResult | None = None
    last_exec: IntegrationExecutionResult | None = None
    last_result_val: IntegrationResultValidationResult | None = None

    rounds = max(0, int(max_retries)) + 1
    for attempt in range(rounds):
        attempt_feedback = list(feedback)
        try:
            plan = planner(
                user_prompt,
                understanding,
                base_url=base_url,
                model=model,
                chat_json_fn=chat_json_fn,
                retry_feedback=attempt_feedback or None,
            )
        except TypeError:
            # Custom build_plan_fn may not accept retry_feedback
            plan = planner(
                user_prompt,
                understanding,
                base_url=base_url,
                model=model,
                chat_json_fn=chat_json_fn,
            )
        except Exception as exc:  # noqa: BLE001
            retry_log.append(
                {
                    "attempt": attempt,
                    "failure_stage": FAILURE_STAGE_PLAN_GENERATION,
                    "failure_codes": ["planner_exception"],
                    "message": str(exc),
                }
            )
            feedback = [
                f"Failure stage: {FAILURE_STAGE_PLAN_GENERATION}",
                f"Planner exception: {type(exc).__name__}: {exc}",
                "Return a valid IntegrationPlan JSON or cannot_plan.",
            ]
            continue

        last_plan = plan
        sig = canonical_integration_plan_signature(plan)
        family = integration_operation_family_signature(plan)
        ops = [s.op for s in plan.steps]
        if attempt == 0:
            first_plan_ops = list(ops)
            first_plan_family = family

        repeated_sig = sig in seen_signatures and plan.status == "planned"
        family_repeated = bool(
            plan.status == "planned"
            and family
            and family != "cannot_plan"
            and family in rejected_families
        )
        if plan.status == "planned":
            seen_signatures.add(sig)
        if repeated_sig:
            duplicate_plan_count += 1
        if family_repeated:
            same_family_repeat_count += 1

        if plan.status == "cannot_plan":
            return IntegrationPipelineResult(
                status="cannot_plan",
                plan=plan,
                retry_log=retry_log,
                metadata=_obs_meta(
                    attempt_count=attempt + 1,
                    retry_count=len(retry_log),
                    first_plan_success=first_plan_success,
                    plan_validation_failure_count=plan_validation_failure_count,
                    execution_failure_count=execution_failure_count,
                    result_validation_failure_count=result_validation_failure_count,
                    duplicate_plan_count=duplicate_plan_count,
                    same_family_repeat_count=same_family_repeat_count,
                    rejected_families=list(rejected_families),
                    first_plan_operations=first_plan_ops,
                    first_plan_family=first_plan_family,
                    operation_family=family,
                    validator_blocked_unsafe_plan=validator_blocked_unsafe_plan,
                    final_status="cannot_plan",
                    selected_operations=ops,
                    source_count=len(sources),
                    warnings=[],
                    repeated_contract_failure=repeated_contract_failure,
                    expected_schema_by_step=expected_schema_by_step,
                    actual_schema_by_step=actual_schema_by_step,
                ),
            )

        if repeated_sig or family_repeated:
            diversity = repeated_integration_family_feedback(family)
            feedback = [
                f"Failure stage: {FAILURE_STAGE_PLAN_GENERATION}",
                *(
                    ["Code: repeated_plan", "Previous plan was rejected and repeated unchanged."]
                    if repeated_sig
                    else []
                ),
                *diversity,
                *feedback,
            ]
            retry_log.append(
                {
                    "attempt": attempt,
                    "failure_stage": FAILURE_STAGE_PLAN_GENERATION,
                    "failure_codes": (
                        ["repeated_plan", "repeated_integration_family"]
                        if repeated_sig
                        else ["repeated_integration_family"]
                    ),
                    "plan_signature": sig,
                    "operation_family": family,
                    "selected_ops": ops,
                }
            )
            if family and family not in rejected_families:
                rejected_families.append(family)
            if attempt < rounds - 1:
                continue

        plan_val = validate_integration_plan(understanding, plan, user_prompt=user_prompt)
        last_plan_val = plan_val
        expected_schema_by_step = [
            {
                "step_id": e.get("step_id"),
                "op": e.get("op"),
                "output": e.get("output"),
                "output_columns": e.get("output_columns"),
                "join_suffixes": e.get("join_suffixes"),
            }
            for e in (plan_val.lineage or [])
            if isinstance(e, dict)
        ]
        if not plan_val.valid:
            plan_validation_failure_count += 1
            codes = [e.code for e in plan_val.errors]
            ftype = classify_integration_failure_codes(codes)
            code_key = tuple(sorted(codes))
            if (
                ftype
                in {
                    FAILURE_TYPE_STRUCTURAL,
                    FAILURE_TYPE_ALIAS,
                }
                and last_structural_codes is not None
                and code_key == last_structural_codes
            ):
                repeated_contract_failure = True
            if ftype in {FAILURE_TYPE_STRUCTURAL, FAILURE_TYPE_ALIAS}:
                last_structural_codes = code_key
            if any(c in _UNSAFE_PLAN_CODES for c in codes):
                validator_blocked_unsafe_plan = True
            if family and family not in rejected_families:
                rejected_families.append(family)
            entry = {
                "attempt": attempt,
                "failure_stage": FAILURE_STAGE_PLAN_VALIDATION,
                "failure_codes": codes,
                "failure_type": ftype,
                "retry_mode": retry_mode_for_failure_type(ftype),
                "plan_signature": sig,
                "operation_family": family,
                "selected_ops": ops,
                "evidence_summary": [e.message for e in plan_val.errors[:5]],
                "same_operation_family_repeat": family_repeated,
                "repeated_structural_contract_failure": repeated_contract_failure,
                "unsafe_blocked": any(c in _UNSAFE_PLAN_CODES for c in codes),
                "expected_schema_by_step": expected_schema_by_step,
            }
            retry_log.append(entry)
            feedback = format_integration_validation_feedback(
                plan_val, previous_plan=plan.to_dict()
            )
            if repeated_contract_failure:
                feedback = [
                    "Code: repeated_structural_contract_failure",
                    "Do not repeat the same unresolved downstream column reference "
                    "or alias/schema contract violation.",
                    *feedback,
                ]
            if family_repeated or repeated_sig:
                feedback = [
                    *repeated_integration_family_feedback(family),
                    *feedback,
                ]
            continue

        first_plan_success = first_plan_success or (attempt == 0)

        execution = execute_integration_plan(sources, plan, plan_val)
        last_exec = execution
        actual_schema_by_step = []
        for sr in getattr(execution, "step_results", None) or []:
            cols = getattr(sr, "columns_after", None)
            if cols is None and isinstance(getattr(sr, "metadata", None), dict):
                cols = sr.metadata.get("output_columns")
            actual_schema_by_step.append(
                {
                    "step_id": getattr(sr, "step_id", None),
                    "op": getattr(sr, "op", None),
                    "output_columns": list(cols) if cols is not None else None,
                }
            )
        if not execution.success:
            execution_failure_count += 1
            code = getattr(execution.error, "code", "execution_failed")
            retryable = code in _RETRYABLE_EXEC_CODES
            if family and family not in rejected_families:
                rejected_families.append(family)
            entry = {
                "attempt": attempt,
                "failure_stage": FAILURE_STAGE_EXECUTION,
                "failure_codes": [code],
                "plan_signature": sig,
                "operation_family": family,
                "selected_ops": ops,
                "retryable": retryable,
                "evidence_summary": [getattr(execution.error, "message", "")],
            }
            retry_log.append(entry)
            feedback = format_integration_execution_feedback(
                execution, previous_plan=plan.to_dict()
            )
            if not retryable and attempt < rounds - 1:
                feedback = [
                    *feedback,
                    "This execution failure may not be recoverable by replanning. "
                    "Prefer cannot_plan if no safer plan is evident.",
                ]
            continue

        result_val = validate_integration_result(
            plan, execution, plan_validation=plan_val
        )
        last_result_val = result_val
        if not result_val.valid:
            result_validation_failure_count += 1
            codes = [e.code for e in result_val.errors]
            if family and family not in rejected_families:
                rejected_families.append(family)
            entry = {
                "attempt": attempt,
                "failure_stage": FAILURE_STAGE_RESULT_VALIDATION,
                "failure_codes": codes,
                "plan_signature": sig,
                "operation_family": family,
                "selected_ops": ops,
                "evidence_summary": [e.message for e in result_val.errors[:5]],
                "warning_codes": [w.code for w in result_val.warnings[:8]],
            }
            retry_log.append(entry)
            feedback = format_integration_result_validation_feedback(
                result_val, previous_plan=plan.to_dict()
            )
            continue

        # Success
        warnings = [w.code for w in result_val.warnings]
        final = execution.final_output
        return IntegrationPipelineResult(
            status="success",
            plan=plan,
            plan_validation=plan_val,
            execution=execution,
            result_validation=result_val,
            retry_log=retry_log,
            final_output=final.copy(deep=True) if isinstance(final, pd.DataFrame) else None,
            metadata=_obs_meta(
                attempt_count=attempt + 1,
                retry_count=len(retry_log),
                first_plan_success=(attempt == 0 and not retry_log),
                plan_validation_failure_count=plan_validation_failure_count,
                execution_failure_count=execution_failure_count,
                result_validation_failure_count=result_validation_failure_count,
                duplicate_plan_count=duplicate_plan_count,
                same_family_repeat_count=same_family_repeat_count,
                rejected_families=list(rejected_families),
                first_plan_operations=first_plan_ops,
                first_plan_family=first_plan_family,
                operation_family=family,
                validator_blocked_unsafe_plan=validator_blocked_unsafe_plan,
                final_status="success",
                selected_operations=ops,
                source_count=len(sources),
                final_shape=(
                    [int(final.shape[0]), int(final.shape[1])]
                    if isinstance(final, pd.DataFrame)
                    else None
                ),
                warnings=warnings,
                repeated_contract_failure=repeated_contract_failure,
                expected_schema_by_step=expected_schema_by_step,
                actual_schema_by_step=actual_schema_by_step,
            ),
        )

    return IntegrationPipelineResult(
        status="failed",
        plan=last_plan,
        plan_validation=last_plan_val,
        execution=last_exec,
        result_validation=last_result_val,
        retry_log=retry_log,
        final_output=None,
        metadata=_obs_meta(
            attempt_count=rounds,
            retry_count=len(retry_log),
            first_plan_success=first_plan_success,
            plan_validation_failure_count=plan_validation_failure_count,
            execution_failure_count=execution_failure_count,
            result_validation_failure_count=result_validation_failure_count,
            duplicate_plan_count=duplicate_plan_count,
            same_family_repeat_count=same_family_repeat_count,
            rejected_families=list(rejected_families),
            first_plan_operations=first_plan_ops,
            first_plan_family=first_plan_family,
            operation_family=(
                integration_operation_family_signature(last_plan) if last_plan else None
            ),
            validator_blocked_unsafe_plan=validator_blocked_unsafe_plan,
            final_status="failed",
            selected_operations=[s.op for s in last_plan.steps] if last_plan else [],
            source_count=len(sources),
            warnings=[],
            exhausted=True,
            repeated_contract_failure=repeated_contract_failure,
            expected_schema_by_step=expected_schema_by_step,
            actual_schema_by_step=actual_schema_by_step,
        ),
    )


def _obs_meta(**kwargs: Any) -> dict[str, Any]:
    meta = {"phase": 22, **kwargs}
    return meta
