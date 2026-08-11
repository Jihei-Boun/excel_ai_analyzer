"""Phase 18: Integration Result Validator (post-execution, no repair).

validate_integration_result(plan, execution, plan_validation?) → ResultValidationResult
Read-only: does not mutate plan, execution frames, or sources.
Reuses Phase 16 AMP_* / LOW_MATCH_* constants.
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np
import pandas as pd

from core.integrate.integration_execution_types import IntegrationExecutionResult
from core.integrate.integration_plan_types import IntegrationPlan, IntegrationStep
from core.integrate.integration_plan_validate import (
    AMP_ERROR_RATIO,
    AMP_WARNING_RATIO,
    LOW_MATCH_WARNING,
)
from core.integrate.integration_result_validation_types import (
    FAILURE_STAGE_EXECUTION,
    FAILURE_STAGE_RESULT_VALIDATION,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    IntegrationResultValidationIssue,
    IntegrationResultValidationResult,
)
from core.integrate.integration_validation_types import IntegrationValidationResult

# Result-only thresholds (not semantic key choice)
INNER_RETENTION_ERROR = 0.02  # output / max(left,right)
INNER_RETENTION_WARNING = 0.10
UNMATCHED_WARNING = 0.50
AMP_ESTIMATE_MISMATCH_FACTOR = 3.0


def validate_integration_result(
    plan: IntegrationPlan | dict[str, Any],
    execution: IntegrationExecutionResult,
    *,
    plan_validation: IntegrationValidationResult | dict[str, Any] | None = None,
) -> IntegrationResultValidationResult:
    """Validate executed integration output against plan + observed metadata."""
    plan_obj = plan if isinstance(plan, IntegrationPlan) else None
    if plan_obj is None:
        from core.integrate.integration_plan_types import integration_plan_from_dict

        plan_obj = integration_plan_from_dict(plan)

    plan_before = copy.deepcopy(plan_obj.to_dict())
    final_snap = (
        execution.final_output.copy(deep=True)
        if isinstance(execution.final_output, pd.DataFrame)
        else None
    )
    dataset_snaps = {
        k: v.copy(deep=True)
        for k, v in (execution.datasets or {}).items()
        if isinstance(v, pd.DataFrame)
    }

    errors: list[IntegrationResultValidationIssue] = []
    warnings: list[IntegrationResultValidationIssue] = []
    infos: list[IntegrationResultValidationIssue] = []

    def err(code: str, message: str, *, step_id: str | None = None, **details: Any) -> None:
        errors.append(
            IntegrationResultValidationIssue(
                code=code,
                severity=SEVERITY_ERROR,
                message=message,
                step_id=step_id,
                details=details,
            )
        )

    def warn(code: str, message: str, *, step_id: str | None = None, **details: Any) -> None:
        warnings.append(
            IntegrationResultValidationIssue(
                code=code,
                severity=SEVERITY_WARNING,
                message=message,
                step_id=step_id,
                details=details,
            )
        )

    def info(code: str, message: str, *, step_id: str | None = None, **details: Any) -> None:
        infos.append(
            IntegrationResultValidationIssue(
                code=code,
                severity=SEVERITY_INFO,
                message=message,
                step_id=step_id,
                details=details,
            )
        )

    # --- Execution-level ---
    if not execution.success or execution.error is not None:
        err(
            "execution_failed",
            "Integration execution did not succeed",
            code_detail=getattr(execution.error, "code", None),
            message_detail=getattr(execution.error, "message", None),
            step_id=getattr(execution.error, "step_id", None),
        )
        result = IntegrationResultValidationResult(
            valid=False,
            errors=errors,
            warnings=warnings,
            infos=infos,
            metadata={"phase": 18},
            failure_stage=FAILURE_STAGE_EXECUTION,
        )
        _assert_immutable(plan_obj, plan_before, execution, final_snap, dataset_snaps)
        return result

    if execution.final_output is None:
        err("missing_final_output", "Execution succeeded but final_output is missing")
    elif not isinstance(execution.final_output, pd.DataFrame):
        err("final_output_not_dataframe", "final_output is not a DataFrame")
    else:
        if execution.final_output.empty:
            warn("empty_final_output", "final_output DataFrame is empty")
        info(
            "final_shape",
            "Final output shape",
            rows=int(len(execution.final_output)),
            cols=int(execution.final_output.shape[1]),
        )
        _validate_final_frame(execution.final_output, err=err, warn=warn, info=info)

    if plan_obj.final_output and plan_obj.final_output not in (execution.datasets or {}):
        err(
            "unresolved_final_output_dataset",
            f"final_output {plan_obj.final_output!r} missing from execution.datasets",
            final_output=plan_obj.final_output,
        )

    # Step contract alignment
    plan_steps = list(plan_obj.steps)
    step_results = list(execution.step_results or [])
    if len(step_results) != len(plan_steps):
        err(
            "step_count_mismatch",
            "Plan step count does not match execution step_results",
            plan_steps=len(plan_steps),
            execution_steps=len(step_results),
        )

    estimated_amp_by_step = _estimated_amp_index(plan_validation)

    for i, step in enumerate(plan_steps):
        if i >= len(step_results):
            err("missing_step_result", f"Missing execution result for {step.id}", step_id=step.id)
            continue
        sr = step_results[i]
        _validate_step_contract(step, sr, err=err)
        if sr.status != "success":
            err(
                "step_not_success",
                f"Step {step.id} status={sr.status!r}",
                step_id=step.id,
                status=sr.status,
            )
            continue
        out_name = step.output
        frame = (execution.datasets or {}).get(out_name)
        if frame is None or not isinstance(frame, pd.DataFrame):
            err(
                "missing_step_output_dataset",
                f"Dataset for step output {out_name!r} missing",
                step_id=step.id,
            )
            continue
        if sr.output_shape is None:
            warn(
                "missing_output_shape_metadata",
                "Step result missing output_shape metadata",
                step_id=step.id,
            )

        if step.op == "join":
            _v_join_result(
                step,
                sr,
                frame,
                estimated_amp=estimated_amp_by_step.get(step.id),
                err=err,
                warn=warn,
                info=info,
            )
        elif step.op == "union_rows":
            _v_union_result(step, sr, frame, execution, err=err, warn=warn, info=info)
        elif step.op == "aggregate":
            _v_aggregate_result(step, sr, frame, err=err, warn=warn, info=info)
        elif step.op == "filter_rows":
            _v_filter_result(step, sr, frame, execution, err=err, warn=warn, info=info)
        elif step.op == "rename_columns":
            _v_rename_result(step, sr, frame, execution, err=err, warn=warn)
        elif step.op == "select_columns":
            _v_select_result(step, sr, frame, err=err)

    _validate_lineage(plan_obj, execution, err=err, warn=warn, info=info)

    result = IntegrationResultValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        infos=infos,
        metadata={
            "phase": 18,
            "amp_warning_ratio": AMP_WARNING_RATIO,
            "amp_error_ratio": AMP_ERROR_RATIO,
            "error_count": len(errors),
            "warning_count": len(warnings),
        },
        failure_stage=FAILURE_STAGE_RESULT_VALIDATION,
    )
    _assert_immutable(plan_obj, plan_before, execution, final_snap, dataset_snaps)
    return result


def _assert_immutable(
    plan_obj: IntegrationPlan,
    plan_before: dict[str, Any],
    execution: IntegrationExecutionResult,
    final_snap: pd.DataFrame | None,
    dataset_snaps: dict[str, pd.DataFrame],
) -> None:
    assert plan_obj.to_dict() == plan_before, "result validator must not mutate plan"
    if final_snap is not None and isinstance(execution.final_output, pd.DataFrame):
        assert execution.final_output.equals(final_snap), "must not mutate final_output"
    for k, snap in dataset_snaps.items():
        cur = execution.datasets.get(k)
        if isinstance(cur, pd.DataFrame):
            assert cur.equals(snap), f"must not mutate dataset {k!r}"


def _estimated_amp_index(
    plan_validation: IntegrationValidationResult | dict[str, Any] | None,
) -> dict[str, float]:
    out: dict[str, float] = {}
    if plan_validation is None:
        return out
    if isinstance(plan_validation, IntegrationValidationResult):
        infos = plan_validation.infos
        for issue in infos:
            if issue.code == "amplification_estimate" and issue.step_id:
                amp = (issue.details or {}).get("amplification_ratio")
                if amp is not None:
                    out[str(issue.step_id)] = float(amp)
        return out

    for i in plan_validation.get("infos") or []:
        if isinstance(i, dict):
            code, sid = i.get("code"), i.get("step_id")
            details = i.get("details") or {}
        else:
            code = getattr(i, "code", None)
            sid = getattr(i, "step_id", None)
            details = getattr(i, "details", {}) or {}
        if code == "amplification_estimate" and sid:
            amp = details.get("amplification_ratio")
            if amp is not None:
                out[str(sid)] = float(amp)
    return out


def _validate_step_contract(step: IntegrationStep, sr: Any, *, err) -> None:
    if sr.step_id and sr.step_id != step.id:
        err(
            "step_id_mismatch",
            f"Plan step id {step.id!r} != execution {sr.step_id!r}",
            step_id=step.id,
            execution_step_id=sr.step_id,
        )
    if sr.op != step.op:
        err(
            "step_op_mismatch",
            f"Plan op {step.op!r} != execution op {sr.op!r}",
            step_id=step.id,
            plan_op=step.op,
            execution_op=sr.op,
        )
    if list(sr.inputs or []) != list(step.inputs):
        err(
            "step_inputs_mismatch",
            "Plan inputs do not match execution inputs",
            step_id=step.id,
            plan_inputs=list(step.inputs),
            execution_inputs=list(sr.inputs or []),
        )
    if sr.output != step.output:
        err(
            "step_output_mismatch",
            "Plan output does not match execution output name",
            step_id=step.id,
            plan_output=step.output,
            execution_output=sr.output,
        )


def _validate_final_frame(df: pd.DataFrame, *, err, warn, info) -> None:
    cols = [str(c) for c in df.columns]
    if len(cols) != len(set(cols)):
        err("duplicate_final_columns", "final_output has duplicate column names", columns=cols)
    # Inf in numeric columns
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s):
            if np.isinf(pd.to_numeric(s, errors="coerce")).any():
                err("final_has_inf", f"final_output column {c!r} contains Inf", column=str(c))
            if s.isna().all() and len(s) > 0:
                warn(
                    "all_null_final_column",
                    f"final_output column {c!r} is entirely null",
                    column=str(c),
                )


def _dtype_family(dtype: Any) -> str:
    if pd.api.types.is_bool_dtype(dtype):
        return "bool"
    if pd.api.types.is_numeric_dtype(dtype):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime"
    if pd.api.types.is_string_dtype(dtype) or pd.api.types.is_object_dtype(dtype):
        # object often means mixed/string
        return "string" if pd.api.types.is_string_dtype(dtype) else "object"
    return "other"


# ---- join ----


def _v_join_result(step, sr, frame: pd.DataFrame, *, estimated_amp, err, warn, info) -> None:
    meta = dict(sr.metadata or {})
    left_rows = int(meta.get("left_rows") or 0)
    right_rows = int(meta.get("right_rows") or 0)
    output_rows = int(meta.get("output_rows") or len(frame))
    amp = meta.get("actual_amplification_ratio")
    if amp is None and max(left_rows, right_rows) > 0:
        amp = float(output_rows) / float(max(left_rows, right_rows))
    amp_f = float(amp) if amp is not None else None
    how = str((step.params or {}).get("how") or meta.get("how") or "inner").lower()

    info(
        "join_actual_stats",
        "Observed join execution stats",
        step_id=step.id,
        left_rows=left_rows,
        right_rows=right_rows,
        output_rows=output_rows,
        actual_amplification_ratio=amp_f,
        how=how,
    )

    if amp_f is not None:
        if amp_f >= AMP_ERROR_RATIO:
            err(
                "extreme_actual_amplification",
                "Actual join amplification is extremely high",
                step_id=step.id,
                actual_amplification_ratio=round(amp_f, 4),
                threshold=AMP_ERROR_RATIO,
                left_rows=left_rows,
                right_rows=right_rows,
                output_rows=output_rows,
            )
        elif amp_f >= AMP_WARNING_RATIO:
            warn(
                "mild_actual_amplification",
                "Actual join amplification is elevated",
                step_id=step.id,
                actual_amplification_ratio=round(amp_f, 4),
                threshold=AMP_WARNING_RATIO,
            )

        if estimated_amp is not None and estimated_amp > 0:
            ratio = amp_f / float(estimated_amp)
            if ratio >= AMP_ESTIMATE_MISMATCH_FACTOR and amp_f >= AMP_WARNING_RATIO:
                code_sev = err if amp_f >= AMP_ERROR_RATIO else warn
                code_sev(
                    "unexpected_join_amplification",
                    "Actual join amplification diverges sharply from pre-execution estimate",
                    step_id=step.id,
                    estimated_amplification=round(float(estimated_amp), 4),
                    actual_amplification_ratio=round(amp_f, 4),
                    mismatch_factor=round(ratio, 4),
                )

    left_u = float(meta.get("left_unmatched_rate") or 0.0)
    right_u = float(meta.get("right_unmatched_rate") or 0.0)
    if left_u >= UNMATCHED_WARNING or right_u >= UNMATCHED_WARNING:
        warn(
            "high_unmatched_rate",
            "Join unmatched rate is high",
            step_id=step.id,
            left_unmatched_rate=left_u,
            right_unmatched_rate=right_u,
            how=how,
        )
    if how == "inner" and max(left_rows, right_rows) > 0:
        retention = float(output_rows) / float(max(left_rows, right_rows))
        if retention < INNER_RETENTION_ERROR:
            err(
                "severe_inner_join_row_loss",
                "Inner join retained almost no rows relative to inputs",
                step_id=step.id,
                retention_ratio=round(retention, 6),
                left_rows=left_rows,
                right_rows=right_rows,
                output_rows=output_rows,
            )
        elif retention < INNER_RETENTION_WARNING:
            warn(
                "severe_inner_join_row_loss",
                "Inner join retained a very small fraction of input rows",
                step_id=step.id,
                retention_ratio=round(retention, 6),
                left_rows=left_rows,
                right_rows=right_rows,
                output_rows=output_rows,
            )
        if left_u >= (1.0 - LOW_MATCH_WARNING) or right_u >= (1.0 - LOW_MATCH_WARNING):
            # For inner, unmatched on indicator after merge is 0 for dropped rows —
            # use retention as coverage proxy already handled. Also check matched rate.
            pass

    # Schema: keys should be present (left key names typically retained)
    left_keys = [str(k) for k in (step.params.get("left_keys") or [])]
    for k in left_keys:
        if k not in frame.columns:
            # may appear only as left key when equal names; if missing → error
            err(
                "join_key_missing_in_output",
                f"Expected join key {k!r} missing from join output",
                step_id=step.id,
                key=k,
            )

    cols_meta = list(meta.get("output_columns") or sr.columns_after or [])
    actual_cols = [str(c) for c in frame.columns]
    if cols_meta and cols_meta != actual_cols:
        warn(
            "join_columns_metadata_mismatch",
            "columns_after metadata does not match actual join output columns",
            step_id=step.id,
            metadata_columns=cols_meta,
            actual_columns=actual_cols,
        )


# ---- union ----


def _v_union_result(
    step, sr, frame: pd.DataFrame, execution, *, err, warn, info
) -> None:
    meta = dict(sr.metadata or {})
    input_rows = meta.get("input_rows") or []
    if isinstance(input_rows, list) and input_rows:
        expected = int(sum(int(x) for x in input_rows))
        actual = int(len(frame))
        if actual != expected:
            err(
                "union_row_count_invariant",
                "union_rows output row count != sum(input rows)",
                step_id=step.id,
                expected_rows=expected,
                actual_rows=actual,
                input_rows=input_rows,
            )
        info(
            "union_row_stats",
            "Union row counts",
            step_id=step.id,
            expected_rows=expected,
            actual_rows=actual,
        )

    policy = str((step.params or {}).get("column_policy") or meta.get("column_policy") or "aligned")
    expected_cols = list(meta.get("column_order") or meta.get("output_columns") or [])
    actual_cols = [str(c) for c in frame.columns]
    if expected_cols and expected_cols != actual_cols:
        err(
            "union_column_mismatch",
            "union output columns/order differ from execution metadata",
            step_id=step.id,
            expected=expected_cols,
            actual=actual_cols,
        )

    input_frames = [
        execution.datasets[n]
        for n in step.inputs
        if n in (execution.datasets or {}) and isinstance(execution.datasets[n], pd.DataFrame)
    ]
    for c in frame.columns:
        out_fam = _dtype_family(frame[c].dtype)
        in_fams = []
        for f in input_frames:
            if c in f.columns:
                in_fams.append(_dtype_family(f[c].dtype))
        if not in_fams:
            continue
        if (
            all(f == "numeric" for f in in_fams)
            and out_fam in {"object", "string"}
        ):
            warn(
                "union_dtype_degradation",
                f"union output column {c!r} degraded from numeric to {out_fam}",
                step_id=step.id,
                column=str(c),
                input_families=in_fams,
                output_family=out_fam,
            )

    null_ratio = float(frame.isna().mean().mean()) if len(frame.columns) else 0.0
    if null_ratio > 0 and policy == "union_with_nulls":
        info(
            "union_null_introduction",
            "Nulls present under union_with_nulls policy (may be expected)",
            step_id=step.id,
            mean_null_ratio=round(null_ratio, 4),
            column_policy=policy,
        )
    elif null_ratio > 0.25 and policy == "aligned":
        warn(
            "union_unexpected_null_increase",
            "High null ratio after aligned union",
            step_id=step.id,
            mean_null_ratio=round(null_ratio, 4),
            column_policy=policy,
        )


# ---- aggregate ----


def _v_aggregate_result(step, sr, frame: pd.DataFrame, *, err, warn, info) -> None:
    group_by = [str(x) for x in (step.params.get("group_by") or [])]
    metrics = step.params.get("metrics") or []
    for g in group_by:
        if g not in frame.columns:
            err(
                "aggregate_group_missing",
                f"group_by column {g!r} missing from aggregate output",
                step_id=step.id,
                column=g,
            )
    aliases = []
    for m in metrics:
        if not isinstance(m, dict):
            continue
        alias = str(m.get("alias") or m.get("column") or "")
        aliases.append(alias)
        if alias and alias not in frame.columns:
            err(
                "missing_metric_output",
                f"Aggregate metric output {alias!r} missing",
                step_id=step.id,
                alias=alias,
            )
        elif alias and alias in frame.columns:
            s = frame[alias]
            vals = pd.to_numeric(s, errors="coerce")
            if len(vals) > 0 and vals.isna().all():
                err(
                    "aggregate_all_nan",
                    f"Metric {alias!r} is all NaN",
                    step_id=step.id,
                    alias=alias,
                )
            arr = vals.to_numpy(dtype="float64", copy=True)
            if np.isinf(arr).any():
                err(
                    "aggregate_has_inf",
                    f"Metric {alias!r} contains Inf",
                    step_id=step.id,
                    alias=alias,
                )

    if group_by and all(g in frame.columns for g in group_by) and len(frame) > 0:
        dup = int(frame.duplicated(subset=group_by).sum())
        if dup:
            err(
                "aggregate_group_not_unique",
                "Aggregate output has duplicate group_by combinations",
                step_id=step.id,
                group_by=group_by,
                duplicate_rows=dup,
            )
        else:
            info(
                "aggregate_grain_ok",
                "Aggregate group grain is unique",
                step_id=step.id,
                group_count=int(len(frame)),
            )

    if frame.empty:
        warn("empty_aggregate_result", "Aggregate output is empty", step_id=step.id)

    in_rows = (sr.metadata or {}).get("input_rows")
    out_rows = int(len(frame))
    if in_rows is not None:
        info(
            "aggregate_row_delta",
            "Aggregate input/output rows",
            step_id=step.id,
            input_rows=in_rows,
            output_rows=out_rows,
        )


# ---- filter ----


def _v_filter_result(step, sr, frame: pd.DataFrame, execution, *, err, warn, info) -> None:
    meta = dict(sr.metadata or {})
    in_rows = meta.get("input_rows")
    out_rows = int(len(frame))
    if in_rows is not None and out_rows > int(in_rows):
        err(
            "filter_row_increase",
            "filter_rows output has more rows than input (impossible)",
            step_id=step.id,
            input_rows=in_rows,
            output_rows=out_rows,
        )
    if out_rows == 0:
        warn("empty_filter_result", "filter_rows produced 0 rows", step_id=step.id)

    # Predicate re-check on output
    conditions = step.params.get("conditions") or []
    for i, cond in enumerate(conditions):
        if not isinstance(cond, dict):
            continue
        op = str(cond.get("operator") or "")
        left_col = str(cond.get("left_column") or cond.get("column") or "")
        if left_col not in frame.columns:
            continue
        left = frame[left_col]
        if cond.get("right_column"):
            rc = str(cond.get("right_column"))
            if rc not in frame.columns:
                err(
                    "filter_predicate_column_missing",
                    f"right_column {rc!r} missing for predicate check",
                    step_id=step.id,
                )
                continue
            right = frame[rc]
        elif "value" in cond:
            right = cond.get("value")
        else:
            continue
        ok = _compare_series(left, op, right).fillna(False)
        if not bool(ok.all()) and len(frame) > 0:
            err(
                "filter_predicate_violation",
                f"Some output rows violate filter condition[{i}]",
                step_id=step.id,
                condition_index=i,
                failing_rows=int((~ok).sum()),
            )

    # columns should match input (filter doesn't drop cols)
    before = None
    if step.inputs:
        inp = step.inputs[0]
        # columns_before on step result
        before = (sr.columns_before or {}).get(inp)
    if before is not None and list(frame.columns) != list(before):
        # filter should preserve columns
        warn(
            "filter_columns_changed",
            "filter_rows changed column set unexpectedly",
            step_id=step.id,
            before=list(before),
            after=[str(c) for c in frame.columns],
        )


def _compare_series(left: pd.Series, op: str, right: Any) -> pd.Series:
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    return pd.Series(True, index=left.index)


# ---- rename / select ----


def _v_rename_result(step, sr, frame: pd.DataFrame, execution, *, err, warn) -> None:
    mapping = step.params.get("mapping") or {}
    meta = dict(sr.metadata or {})
    in_rows = meta.get("input_rows")
    if in_rows is not None and int(len(frame)) != int(in_rows):
        err(
            "rename_row_count_changed",
            "rename_columns must preserve row count",
            step_id=step.id,
            input_rows=in_rows,
            output_rows=int(len(frame)),
        )
    for new in mapping.values():
        if str(new) not in frame.columns:
            err(
                "rename_target_missing",
                f"Renamed target column {new!r} missing from output",
                step_id=step.id,
                target=str(new),
            )
    cols = [str(c) for c in frame.columns]
    if len(cols) != len(set(cols)):
        err("rename_duplicate_columns", "rename produced duplicate columns", step_id=step.id)


def _v_select_result(step, sr, frame: pd.DataFrame, *, err) -> None:
    expected = [str(c) for c in (step.params.get("columns") or [])]
    actual = [str(c) for c in frame.columns]
    if actual != expected:
        err(
            "select_columns_mismatch",
            "select_columns output columns/order do not match plan",
            step_id=step.id,
            expected=expected,
            actual=actual,
        )
    meta = dict(sr.metadata or {})
    in_rows = meta.get("input_rows")
    if in_rows is not None and int(len(frame)) != int(in_rows):
        err(
            "select_row_count_changed",
            "select_columns must preserve row count",
            step_id=step.id,
            input_rows=in_rows,
            output_rows=int(len(frame)),
        )


# ---- lineage ----


def _validate_lineage(plan: IntegrationPlan, execution: IntegrationExecutionResult, *, err, warn, info) -> None:
    lineage = list(execution.lineage or [])
    if plan.steps and not lineage:
        warn("missing_execution_lineage", "Execution lineage is empty")
        return
    produced = {s.output for s in plan.steps}
    sources = set((execution.datasets or {}).keys()) - produced
    # Every step output should appear in lineage or be tracked
    lin_outputs = {str(x.get("output")) for x in lineage if isinstance(x, dict)}
    for step in plan.steps:
        if step.output not in lin_outputs:
            warn(
                "lineage_missing_step",
                f"No lineage entry for step output {step.output!r}",
                step_id=step.id,
            )
        for inp in step.inputs:
            if inp not in sources and inp not in produced and inp not in (execution.datasets or {}):
                err(
                    "lineage_broken_input",
                    f"Step input {inp!r} not traceable",
                    step_id=step.id,
                    input=inp,
                )
        if step.op == "aggregate":
            entry = next((x for x in lineage if x.get("step_id") == step.id), None)
            if entry and not entry.get("metrics"):
                warn(
                    "lineage_missing_metrics",
                    "Aggregate lineage missing metrics",
                    step_id=step.id,
                )
        if step.op == "join":
            entry = next((x for x in lineage if x.get("step_id") == step.id), None)
            if entry and not entry.get("key_map"):
                warn(
                    "lineage_missing_join_keys",
                    "Join lineage missing key_map",
                    step_id=step.id,
                )
    if plan.final_output:
        info(
            "final_lineage_target",
            f"final_output={plan.final_output}",
            final_output=plan.final_output,
            in_lineage=plan.final_output in lin_outputs or plan.final_output in produced,
        )
