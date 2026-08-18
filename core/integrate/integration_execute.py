"""Phase 17: Deterministic IntegrationPlan Executor (execution only).

execute_integration_plan(sources, plan, validation_result) → IntegrationExecutionResult

- Gate: status==planned AND validation.valid
- No semantic autocomplete / key rewrite / op fallback / LLM
- Does not mutate plan or source DataFrames
- Does not call merge_engine key inference / align helpers
"""

from __future__ import annotations

import copy
from typing import Any

import pandas as pd

from core.integrate.integration_execution_types import (
    IntegrationExecutionError,
    IntegrationExecutionResult,
    IntegrationStepExecutionResult,
)
from core.integrate.integration_plan_types import (
    AGGREGATE_FUNCTIONS,
    FILTER_OPERATORS,
    INTEGRATION_ATOMIC_OPS,
    JOIN_HOW,
    IntegrationPlan,
    IntegrationStep,
)
from core.integrate.integration_validation_types import IntegrationValidationResult

from core.integrate.integration_contracts import JOIN_SUFFIXES


def execute_integration_plan(
    sources: dict[str, pd.DataFrame],
    plan: IntegrationPlan | dict[str, Any],
    validation_result: IntegrationValidationResult | dict[str, Any] | None,
) -> IntegrationExecutionResult:
    """Execute a validated IntegrationPlan deterministically.

    Never mutates ``sources`` or ``plan``. Never rewrites keys/ops/metrics.
    """
    plan_obj = _coerce_plan(plan)
    plan_before = copy.deepcopy(plan_obj.to_dict())
    source_snapshots = {k: v.copy(deep=True) for k, v in sources.items()}

    gate_err = _check_execution_gate(plan_obj, validation_result)
    if gate_err is not None:
        return IntegrationExecutionResult(
            success=False,
            error=gate_err,
            metadata={"phase": 17, "gate": "rejected"},
        )

    # Registry: copies only — never the caller's frames
    datasets: dict[str, pd.DataFrame] = {
        str(k): v.copy(deep=True) for k, v in sources.items()
    }
    step_results: list[IntegrationStepExecutionResult] = []
    lineage: list[dict[str, Any]] = []

    for step in plan_obj.steps:
        try:
            step_result = _execute_step(step, datasets)
        except IntegrationExecutionError as exc:
            step_results.append(
                IntegrationStepExecutionResult(
                    step_id=step.id,
                    op=step.op,
                    inputs=list(step.inputs),
                    output=step.output,
                    status="failed",
                    error=exc,
                )
            )
            assert plan_obj.to_dict() == plan_before
            _assert_sources_unchanged(sources, source_snapshots)
            return IntegrationExecutionResult(
                success=False,
                datasets=_copy_registry(datasets),
                step_results=step_results,
                metadata={
                    "phase": 17,
                    "source_count": len(sources),
                    "step_count": len(plan_obj.steps),
                    "completed_steps": len([s for s in step_results if s.status == "success"]),
                    "failed_step_id": step.id,
                },
                lineage=lineage,
                error=exc,
            )
        except Exception as exc:  # noqa: BLE001 — wrap unexpected pandas/runtime
            wrapped = IntegrationExecutionError(
                code="runtime_error",
                message=f"Unexpected runtime failure in step {step.id}: {exc}",
                step_id=step.id,
                op=step.op,
                details={
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
            )
            step_results.append(
                IntegrationStepExecutionResult(
                    step_id=step.id,
                    op=step.op,
                    inputs=list(step.inputs),
                    output=step.output,
                    status="failed",
                    error=wrapped,
                )
            )
            assert plan_obj.to_dict() == plan_before
            _assert_sources_unchanged(sources, source_snapshots)
            return IntegrationExecutionResult(
                success=False,
                datasets=_copy_registry(datasets),
                step_results=step_results,
                metadata={
                    "phase": 17,
                    "source_count": len(sources),
                    "step_count": len(plan_obj.steps),
                    "failed_step_id": step.id,
                },
                lineage=lineage,
                error=wrapped,
            )

        datasets[step.output] = step_result.metadata.pop("_frame")  # type: ignore[index]
        if step_result.lineage:
            lineage.append(dict(step_result.lineage))
        step_results.append(step_result)

    final_name = plan_obj.final_output
    if not final_name or final_name not in datasets:
        err = IntegrationExecutionError(
            code="missing_final_output",
            message=f"final_output {final_name!r} not found in datasets after execution",
            details={"available": sorted(datasets.keys())},
        )
        assert plan_obj.to_dict() == plan_before
        _assert_sources_unchanged(sources, source_snapshots)
        return IntegrationExecutionResult(
            success=False,
            datasets=_copy_registry(datasets),
            step_results=step_results,
            metadata={"phase": 17},
            lineage=lineage,
            error=err,
        )

    final_df = datasets[final_name].copy(deep=True)
    assert plan_obj.to_dict() == plan_before
    _assert_sources_unchanged(sources, source_snapshots)

    return IntegrationExecutionResult(
        success=True,
        final_output=final_df,
        final_output_name=final_name,
        datasets=_copy_registry(datasets),
        step_results=step_results,
        metadata={
            "phase": 17,
            "source_count": len(sources),
            "step_count": len(plan_obj.steps),
            "final_row_count": int(len(final_df)),
            "final_column_count": int(final_df.shape[1]),
            "final_columns": [str(c) for c in final_df.columns],
        },
        lineage=lineage,
        error=None,
    )


def _check_execution_gate(
    plan: IntegrationPlan,
    validation_result: IntegrationValidationResult | dict[str, Any] | None,
) -> IntegrationExecutionError | None:
    if validation_result is None:
        return IntegrationExecutionError(
            code="validation_required",
            message="Executor requires IntegrationValidationResult; validation was not provided",
        )
    if isinstance(validation_result, IntegrationValidationResult):
        valid = bool(validation_result.valid)
        val_meta = dict(validation_result.metadata)
    elif isinstance(validation_result, dict):
        valid = bool(validation_result.get("valid"))
        val_meta = dict(validation_result.get("metadata") or {})
    else:
        return IntegrationExecutionError(
            code="invalid_validation_result",
            message="validation_result must be IntegrationValidationResult or dict",
        )

    if plan.status != "planned":
        return IntegrationExecutionError(
            code="execution_gate_rejected",
            message=f"Cannot execute plan with status={plan.status!r} (require planned)",
            details={"status": plan.status, "validation_valid": valid},
        )
    if not valid:
        return IntegrationExecutionError(
            code="execution_gate_rejected",
            message="Cannot execute plan: IntegrationValidationResult.valid is False",
            details={"status": plan.status, "validation_valid": False, "validation_meta": val_meta},
        )
    return None


def _coerce_plan(plan: IntegrationPlan | dict[str, Any]) -> IntegrationPlan:
    if isinstance(plan, IntegrationPlan):
        return plan
    from core.integrate.integration_plan_types import integration_plan_from_dict

    return integration_plan_from_dict(plan)


def _copy_registry(datasets: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {k: v.copy(deep=True) for k, v in datasets.items()}


def _assert_sources_unchanged(
    sources: dict[str, pd.DataFrame],
    snapshots: dict[str, pd.DataFrame],
) -> None:
    for name, snap in snapshots.items():
        cur = sources[name]
        if not cur.equals(snap):
            raise AssertionError(f"Executor mutated source DataFrame {name!r}")


def _execute_step(
    step: IntegrationStep,
    datasets: dict[str, pd.DataFrame],
) -> IntegrationStepExecutionResult:
    if step.op not in INTEGRATION_ATOMIC_OPS:
        raise IntegrationExecutionError(
            code="unsupported_operation",
            message=f"Unsupported operation {step.op!r}",
            step_id=step.id,
            op=step.op,
        )

    frames: list[pd.DataFrame] = []
    input_shapes: dict[str, tuple[int, int]] = {}
    columns_before: dict[str, list[str]] = {}
    for name in step.inputs:
        if name not in datasets:
            raise IntegrationExecutionError(
                code="missing_dataset",
                message=f"Input dataset {name!r} not found",
                step_id=step.id,
                op=step.op,
                details={"input": name, "available": sorted(datasets.keys())},
            )
        df = datasets[name]
        frames.append(df)
        input_shapes[name] = (int(len(df)), int(df.shape[1]))
        columns_before[name] = [str(c) for c in df.columns]

    if step.op == "rename_columns":
        out, meta, lin = _op_rename(step, frames[0])
    elif step.op == "filter_rows":
        out, meta, lin = _op_filter(step, frames[0])
    elif step.op == "union_rows":
        out, meta, lin = _op_union(step, frames)
    elif step.op == "join":
        out, meta, lin = _op_join(step, frames)
    elif step.op == "aggregate":
        out, meta, lin = _op_aggregate(step, frames[0])
    elif step.op == "select_columns":
        out, meta, lin = _op_select(step, frames[0])
    else:
        raise IntegrationExecutionError(
            code="unsupported_operation",
            message=f"Unsupported operation {step.op!r}",
            step_id=step.id,
            op=step.op,
        )

    meta = dict(meta)
    meta["_frame"] = out
    meta["input_row_counts"] = {k: v[0] for k, v in input_shapes.items()}
    meta["output_row_count"] = int(len(out))
    meta["row_delta"] = int(len(out)) - sum(v[0] for v in input_shapes.values()) // max(
        len(input_shapes), 1
    )
    if len(input_shapes) == 1:
        only = next(iter(input_shapes.values()))[0]
        meta["row_delta"] = int(len(out)) - only
    meta["input_columns"] = columns_before
    meta["output_columns"] = [str(c) for c in out.columns]
    meta["column_delta"] = int(out.shape[1]) - (
        next(iter(input_shapes.values()))[1] if len(input_shapes) == 1 else 0
    )

    return IntegrationStepExecutionResult(
        step_id=step.id,
        op=step.op,
        inputs=list(step.inputs),
        output=step.output,
        status="success",
        input_shapes=input_shapes,
        output_shape=(int(len(out)), int(out.shape[1])),
        columns_before=columns_before,
        columns_after=[str(c) for c in out.columns],
        metadata=meta,
        lineage=lin,
    )


# ---------------------------------------------------------------------------
# Atomic ops
# ---------------------------------------------------------------------------


def _op_rename(
    step: IntegrationStep, df: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    mapping = step.params.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise IntegrationExecutionError(
            code="malformed_params",
            message="rename_columns requires params.mapping",
            step_id=step.id,
            op=step.op,
        )
    mapping_s = {str(k): str(v) for k, v in mapping.items()}
    missing = [k for k in mapping_s if k not in df.columns]
    if missing:
        raise IntegrationExecutionError(
            code="missing_column",
            message=f"rename_columns missing columns: {missing}",
            step_id=step.id,
            op=step.op,
            details={"missing": missing},
        )
    out = df.rename(columns=mapping_s)
    lin = {
        "step_id": step.id,
        "op": step.op,
        "inputs": list(step.inputs),
        "output": step.output,
        "column_map": {
            f"{step.inputs[0]}.{old}": f"{step.output}.{new}"
            for old, new in mapping_s.items()
        },
    }
    return out, {"mapping": mapping_s}, lin


def _op_filter(
    step: IntegrationStep, df: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    conditions = step.params.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise IntegrationExecutionError(
            code="malformed_params",
            message="filter_rows requires params.conditions",
            step_id=step.id,
            op=step.op,
        )
    mask = pd.Series(True, index=df.index)
    used_cols: list[str] = []
    for i, cond in enumerate(conditions):
        if not isinstance(cond, dict):
            raise IntegrationExecutionError(
                code="malformed_params",
                message=f"conditions[{i}] must be object",
                step_id=step.id,
                op=step.op,
            )
        col = str(cond.get("column") or "")
        op = str(cond.get("operator") or cond.get("op") or "").lower()
        if col not in df.columns:
            raise IntegrationExecutionError(
                code="missing_column",
                message=f"filter column {col!r} not found",
                step_id=step.id,
                op=step.op,
                details={"column": col},
            )
        if op not in FILTER_OPERATORS:
            raise IntegrationExecutionError(
                code="unsupported_filter_operator",
                message=f"Unsupported filter operator {op!r}",
                step_id=step.id,
                op=step.op,
                details={"operator": op, "allowed": sorted(FILTER_OPERATORS)},
            )
        used_cols.append(col)
        left_name = str(cond.get("left_column") or col)
        if left_name != col:
            if left_name not in df.columns:
                raise IntegrationExecutionError(
                    code="missing_column",
                    message=f"filter left_column {left_name!r} not found",
                    step_id=step.id,
                    op=step.op,
                    details={"left_column": left_name},
                )
            left = df[left_name]
            used_cols.append(left_name)
        else:
            left = df[col]
        # Explicit forms only. Never promote value→column because the string
        # matches a column name (e.g. value="안전재고").
        right_col = cond.get("right_column")
        if right_col is not None and str(right_col).strip():
            rc = str(right_col).strip()
            if rc not in df.columns:
                raise IntegrationExecutionError(
                    code="missing_column",
                    message=f"filter right_column {rc!r} not found",
                    step_id=step.id,
                    op=step.op,
                    details={"right_column": rc},
                )
            right = df[rc]
            used_cols.append(rc)
        elif "value" in cond:
            right = cond.get("value")
        else:
            raise IntegrationExecutionError(
                code="malformed_params",
                message="filter condition needs value or explicit right_column",
                step_id=step.id,
                op=step.op,
            )

        part = _compare(left, op, right)
        # Keep only rows where comparison is True; NA/unknown excluded.
        part = part.fillna(False)
        mask = mask & part

    out = df.loc[mask].copy()
    lin = {
        "step_id": step.id,
        "op": step.op,
        "inputs": list(step.inputs),
        "output": step.output,
        "columns_used": [f"{step.inputs[0]}.{c}" for c in used_cols],
        "conditions": conditions,
    }
    meta = {
        "input_rows": int(len(df)),
        "output_rows": int(len(out)),
        "filtered_row_count": int(len(df) - len(out)),
        "null_policy": "comparison_true_only",
    }
    return out, meta, lin


def _compare(left: pd.Series, op: str, right: Any) -> pd.Series:
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
    raise ValueError(f"unsupported op {op}")


def _op_union(
    step: IntegrationStep, frames: list[pd.DataFrame]
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    if len(frames) < 2:
        raise IntegrationExecutionError(
            code="malformed_params",
            message="union_rows requires ≥2 inputs",
            step_id=step.id,
            op=step.op,
        )
    policy = str(step.params.get("column_policy") or "aligned").lower()
    if policy not in {"aligned", "intersection", "union_with_nulls"}:
        raise IntegrationExecutionError(
            code="malformed_params",
            message=f"invalid column_policy {policy!r}",
            step_id=step.id,
            op=step.op,
        )

    prepared = [f.copy() for f in frames]
    if policy == "intersection":
        common = list(prepared[0].columns)
        for f in prepared[1:]:
            common = [c for c in common if c in f.columns]
        if not common:
            raise IntegrationExecutionError(
                code="union_empty_intersection",
                message="union_rows intersection policy produced no common columns",
                step_id=step.id,
                op=step.op,
            )
        prepared = [f[common].copy() for f in prepared]
        column_order = list(common)
    else:
        # aligned / union_with_nulls: first-input column order, then stable extras
        column_order = _union_column_order(prepared)

    aligned = []
    for f in prepared:
        missing = [c for c in column_order if c not in f.columns]
        piece = f.reindex(columns=column_order)
        if policy == "aligned" and missing:
            # aligned still null-fills extras if schemas differ (validator should
            # have warned/errored); no rename/heuristic alignment.
            pass
        aligned.append(piece)

    out = pd.concat(aligned, axis=0, ignore_index=True, sort=False)
    out = out.reindex(columns=column_order)

    lin = {
        "step_id": step.id,
        "op": step.op,
        "inputs": list(step.inputs),
        "output": step.output,
        "column_policy": policy,
        "columns": column_order,
    }
    meta = {
        "column_policy": policy,
        "input_rows": [int(len(f)) for f in frames],
        "output_rows": int(len(out)),
        "column_order": column_order,
        "schema_changes": {
            "input_column_sets": [[str(c) for c in f.columns] for f in frames],
            "output_columns": column_order,
        },
    }
    return out, meta, lin


def _union_column_order(frames: list[pd.DataFrame]) -> list[str]:
    order: list[str] = []
    seen: set[str] = set()
    for f in frames:
        for c in f.columns:
            name = str(c)
            if name not in seen:
                seen.add(name)
                order.append(name)
    return order


def _op_join(
    step: IntegrationStep, frames: list[pd.DataFrame]
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    if len(frames) != 2:
        raise IntegrationExecutionError(
            code="malformed_params",
            message="join requires exactly 2 inputs [left, right]",
            step_id=step.id,
            op=step.op,
        )
    left = frames[0].copy()
    right = frames[1].copy()
    left_name, right_name = step.inputs[0], step.inputs[1]

    left_keys = [str(x) for x in (step.params.get("left_keys") or [])]
    right_keys = [str(x) for x in (step.params.get("right_keys") or [])]
    how = str(step.params.get("how") or "").lower()
    if not left_keys or not right_keys:
        raise IntegrationExecutionError(
            code="malformed_params",
            message="join requires left_keys and right_keys",
            step_id=step.id,
            op=step.op,
        )
    if how not in JOIN_HOW:
        raise IntegrationExecutionError(
            code="malformed_params",
            message=f"invalid join how={how!r}",
            step_id=step.id,
            op=step.op,
        )
    for k in left_keys:
        if k not in left.columns:
            raise IntegrationExecutionError(
                code="missing_column",
                message=f"left join key {k!r} missing",
                step_id=step.id,
                op=step.op,
                details={"column": k, "side": "left"},
            )
    for k in right_keys:
        if k not in right.columns:
            raise IntegrationExecutionError(
                code="missing_column",
                message=f"right join key {k!r} missing",
                step_id=step.id,
                op=step.op,
                details={"column": k, "side": "right"},
            )

    left_rows = int(len(left))
    right_rows = int(len(right))

    # Pure pandas merge — no merge_engine, no key inference, no direction flip.
    merged = pd.merge(
        left,
        right,
        how=how,
        left_on=left_keys,
        right_on=right_keys,
        suffixes=JOIN_SUFFIXES,
        indicator=True,
    )
    indicator = merged["_merge"]
    out = merged.drop(columns=["_merge"])

    both = int((indicator == "both").sum())
    left_only = int((indicator == "left_only").sum())
    right_only = int((indicator == "right_only").sum())
    output_rows = int(len(out))
    base = max(left_rows, right_rows, 1)
    amp = float(output_rows) / float(base)

    left_unmatched_rate = (left_only / left_rows) if left_rows else 0.0
    right_unmatched_rate = (right_only / right_rows) if right_rows else 0.0

    meta = {
        "left_rows": left_rows,
        "right_rows": right_rows,
        "output_rows": output_rows,
        "actual_amplification_ratio": amp,
        "left_unmatched_count": left_only,
        "right_unmatched_count": right_only,
        "left_unmatched_rate": left_unmatched_rate,
        "right_unmatched_rate": right_unmatched_rate,
        "matched_row_count": both,
        "how": how,
        "left_keys": left_keys,
        "right_keys": right_keys,
        "suffixes": list(JOIN_SUFFIXES),
        "left_input": left_name,
        "right_input": right_name,
    }
    lin = {
        "step_id": step.id,
        "op": step.op,
        "inputs": list(step.inputs),
        "output": step.output,
        "join_how": how,
        "key_map": [
            {
                "left": f"{left_name}.{lk}",
                "right": f"{right_name}.{rk}",
                "output": f"{step.output}.{lk if lk == rk else lk}",
            }
            for lk, rk in zip(left_keys, right_keys)
        ],
        "suffix_policy": {"left": JOIN_SUFFIXES[0], "right": JOIN_SUFFIXES[1]},
    }
    return out, meta, lin


def _op_aggregate(
    step: IntegrationStep, df: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    group_by = step.params.get("group_by")
    metrics = step.params.get("metrics")
    if not isinstance(group_by, list):
        raise IntegrationExecutionError(
            code="malformed_params",
            message="aggregate requires params.group_by list",
            step_id=step.id,
            op=step.op,
        )
    if not isinstance(metrics, list) or not metrics:
        raise IntegrationExecutionError(
            code="malformed_params",
            message="aggregate requires non-empty params.metrics",
            step_id=step.id,
            op=step.op,
        )
    group_s = [str(x) for x in group_by]
    for g in group_s:
        if g not in df.columns:
            raise IntegrationExecutionError(
                code="missing_column",
                message=f"aggregate group_by column {g!r} missing",
                step_id=step.id,
                op=step.op,
                details={"column": g},
            )

    named: dict[str, tuple[str, str]] = {}
    metric_lineage: list[dict[str, Any]] = []
    for i, m in enumerate(metrics):
        if not isinstance(m, dict):
            raise IntegrationExecutionError(
                code="malformed_params",
                message=f"metrics[{i}] must be object",
                step_id=step.id,
                op=step.op,
            )
        col = str(m.get("column") or "").strip()
        fn = str(m.get("function") or m.get("fn") or "").strip().lower()
        if not col:
            raise IntegrationExecutionError(
                code="malformed_params",
                message=f"metrics[{i}] missing column",
                step_id=step.id,
                op=step.op,
            )
        if not fn:
            raise IntegrationExecutionError(
                code="malformed_params",
                message=f"metrics[{i}] missing function (no silent default)",
                step_id=step.id,
                op=step.op,
            )
        if fn not in AGGREGATE_FUNCTIONS:
            raise IntegrationExecutionError(
                code="unsupported_aggregation",
                message=f"Unsupported aggregation {fn!r}",
                step_id=step.id,
                op=step.op,
                details={"function": fn},
            )
        if col not in df.columns:
            raise IntegrationExecutionError(
                code="missing_column",
                message=f"aggregate metric column {col!r} missing",
                step_id=step.id,
                op=step.op,
                details={"column": col},
            )
        # Alias: shared structural contract (integration_contracts).
        from core.integrate.integration_contracts import resolve_aggregate_alias

        alias = resolve_aggregate_alias(m)
        if alias in named:
            raise IntegrationExecutionError(
                code="aggregate_alias_collision",
                message=f"Duplicate aggregate output name {alias!r}",
                step_id=step.id,
                op=step.op,
                details={"alias": alias},
            )
        named[alias] = (col, fn)
        metric_lineage.append(
            {
                "source": f"{step.inputs[0]}.{col}",
                "function": fn,
                "output": f"{step.output}.{alias}",
            }
        )

    # Deterministic group order (pandas groupby sort=True).
    if group_s:
        grouped = df.groupby(group_s, sort=True, dropna=False)
        out = grouped.agg(**{alias: pd.NamedAgg(column=col, aggfunc=fn) for alias, (col, fn) in named.items()})
        out = out.reset_index()
    else:
        # Empty group_by is allowed by Phase 15 structural contract (list may be []).
        # No silent whole-frame invent — execute as global aggregation exactly.
        aggs = {alias: df[col].agg(fn) for alias, (col, fn) in named.items()}
        out = pd.DataFrame([aggs])

    lin = {
        "step_id": step.id,
        "op": step.op,
        "inputs": list(step.inputs),
        "output": step.output,
        "group_by": group_s,
        "metrics": metric_lineage,
    }
    meta = {
        "input_rows": int(len(df)),
        "output_rows": int(len(out)),
        "group_count": int(len(out)),
        "group_by": group_s,
        "metrics": [
            {"column": col, "function": fn, "alias": alias}
            for alias, (col, fn) in named.items()
        ],
    }
    return out, meta, lin


def _op_select(
    step: IntegrationStep, df: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    columns = step.params.get("columns")
    if not isinstance(columns, list) or not columns:
        raise IntegrationExecutionError(
            code="malformed_params",
            message="select_columns requires params.columns",
            step_id=step.id,
            op=step.op,
        )
    cols = [str(c) for c in columns]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise IntegrationExecutionError(
            code="missing_column",
            message=f"select_columns missing: {missing}",
            step_id=step.id,
            op=step.op,
            details={"missing": missing},
        )
    out = df.loc[:, cols].copy()
    lin = {
        "step_id": step.id,
        "op": step.op,
        "inputs": list(step.inputs),
        "output": step.output,
        "column_map": {
            f"{step.inputs[0]}.{c}": f"{step.output}.{c}" for c in cols
        },
    }
    return out, {"columns": cols}, lin
