"""Shared IntegrationPlan structural contracts (Phase 21–22).

Single source of truth for:
- aggregate alias defaults / resolution
- join collision suffix policy
- expected intermediate schema column naming

Used by parser, Plan Validator, Executor, Result Validator.

Rules are representation-level only — never invent semantic names
(e.g. amount → 매출합계 is forbidden).
"""

from __future__ import annotations

from typing import Any, Iterable


# Mechanical join suffix when non-key column names collide (pandas merge suffixes).
# MUST match Executor merge(suffixes=...).
JOIN_SUFFIX_LEFT = "_left"
JOIN_SUFFIX_RIGHT = "_right"
JOIN_SUFFIXES: tuple[str, str] = (JOIN_SUFFIX_LEFT, JOIN_SUFFIX_RIGHT)


def default_aggregate_alias(column: str, function: str) -> str:
    """Deterministic structural default when Planner omits alias.

    Policy B (Phase 21–22): alias optional + shared default = source column name.
    """
    col = str(column or "").strip()
    _ = function  # reserved for future structural conventions (e.g. col__fn)
    return col


def resolve_aggregate_alias(metric: dict[str, Any]) -> str:
    """Resolve output column name for one aggregate metric dict."""
    col = str(metric.get("column") or "").strip()
    fn = str(metric.get("function") or metric.get("fn") or "").strip().lower()
    alias_raw = metric.get("alias")
    if alias_raw is not None and str(alias_raw).strip():
        return str(alias_raw).strip()
    return default_aggregate_alias(col, fn)


def materialize_aggregate_metric(metric: dict[str, Any]) -> dict[str, Any]:
    """Return metric dict with alias always present (structural normalize)."""
    col = str(metric.get("column") or "").strip()
    fn = str(metric.get("function") or metric.get("fn") or "").strip().lower()
    out: dict[str, Any] = {"column": col, "function": fn}
    out["alias"] = resolve_aggregate_alias(
        {"column": col, "function": fn, "alias": metric.get("alias")}
    )
    return out


def join_output_column_names(
    left_columns: Iterable[str],
    right_columns: Iterable[str],
    *,
    left_keys: list[str],
    right_keys: list[str],
) -> list[str]:
    """Structural join output column names matching Executor pandas merge + JOIN_SUFFIXES.

    Uses empty DataFrames so naming cannot drift from ``pd.merge(..., suffixes=)``.
    Non-key name collisions become ``{name}_left`` / ``{name}_right``.
    """
    import pandas as pd

    left_cols = [str(c) for c in left_columns]
    right_cols = [str(c) for c in right_columns]
    lk = [str(k) for k in left_keys]
    rk = [str(k) for k in right_keys]
    if not lk or not rk or len(lk) != len(rk):
        # Malformed keys — return union of names (validator will error separately)
        return list(dict.fromkeys([*left_cols, *right_cols]))

    left = pd.DataFrame({c: pd.Series(dtype="object") for c in left_cols})
    right = pd.DataFrame({c: pd.Series(dtype="object") for c in right_cols})
    for k in lk:
        if k not in left.columns:
            left[k] = pd.Series(dtype="object")
    for k in rk:
        if k not in right.columns:
            right[k] = pd.Series(dtype="object")
    merged = pd.merge(
        left,
        right,
        how="inner",
        left_on=lk,
        right_on=rk,
        suffixes=JOIN_SUFFIXES,
    )
    return [str(c) for c in merged.columns]


def aggregate_output_column_names(
    group_by: Iterable[str],
    metrics: Iterable[dict[str, Any]],
) -> list[str]:
    """Declared aggregate output columns (group_by + resolved aliases)."""
    cols = [str(g) for g in group_by if str(g).strip()]
    for m in metrics:
        if isinstance(m, dict):
            alias = resolve_aggregate_alias(m)
            if alias:
                cols.append(alias)
    return cols


# Retry / observability failure types (evidence labels — not answer keys)
FAILURE_TYPE_SEMANTIC = "semantic_failure"
FAILURE_TYPE_STRUCTURAL = "structural_contract_failure"
FAILURE_TYPE_AMBIGUITY = "ambiguity_failure"
FAILURE_TYPE_RESULT = "result_invariant_failure"
FAILURE_TYPE_ALIAS = "alias_contract_failure"

_AMBIGUITY_CODES = frozenset(
    {
        "ambiguous_key_selection",
        "insufficient_evidence_forced_join",
        "join_against_unrelated",
        "union_incompatible_schema",  # often follows unrelated / incompatible stacking
    }
)
_STRUCTURAL_CODES = frozenset(
    {
        "nonexistent_column",
        "missing_column",
        "missing_dataset",
        "malformed_params",
        "unsupported_operation",
        "unsupported_filter_operator",
        "unsupported_aggregation",
        "aggregate_alias_collision",
        "invalid_metric",
        "union_empty_intersection",
        "unknown_input",
        "planner_parse_failed",
        "missing_metric_output",
        "aggregate_group_missing",
        "empty_select",
        "duplicate_select_column",
        "final_grain_contradiction",
        "final_required_field_missing",
        "required_field_permanently_lost",
        "required_field_not_materializable",
        "join_key_dropped_in_final_projection",
        "invalid_final_grain",
    }
)
_RESULT_CODES = frozenset(
    {
        "extreme_row_amplification",
        "many_to_many_join_risk",
        "unexpected_row_loss",
        "schema_contract_violation",
        "result_amplification",
        "final_required_column_missing",
    }
)
_ALIAS_CODES = frozenset(
    {
        "missing_metric_output",
        "aggregate_alias_collision",
        "nonexistent_column",  # often downstream alias reference
    }
)

_FINAL_CONTRACT_CODES = frozenset(
    {
        "final_grain_contradiction",
        "final_required_field_missing",
        "required_field_permanently_lost",
        "required_field_not_materializable",
        "final_required_column_missing",
        "invalid_final_grain",
        "join_key_dropped_in_final_projection",
    }
)


def classify_integration_failure_codes(codes: list[str] | None) -> str:
    """Map validation/execution error codes → failure type for retry feedback."""
    codes_s = {str(c) for c in (codes or [])}
    if codes_s & _AMBIGUITY_CODES:
        return FAILURE_TYPE_AMBIGUITY
    if codes_s & _RESULT_CODES:
        return FAILURE_TYPE_RESULT
    if codes_s & {"missing_metric_output", "aggregate_alias_collision"}:
        return FAILURE_TYPE_ALIAS
    if codes_s & _FINAL_CONTRACT_CODES:
        # Prefer structural repair on first hit; pipeline may escalate to regenerate.
        return FAILURE_TYPE_STRUCTURAL
    if codes_s & _STRUCTURAL_CODES:
        return FAILURE_TYPE_STRUCTURAL
    if not codes_s:
        return FAILURE_TYPE_SEMANTIC
    return FAILURE_TYPE_SEMANTIC


def is_final_contract_failure(codes: list[str] | None) -> bool:
    return bool({str(c) for c in (codes or [])} & _FINAL_CONTRACT_CODES)


def retry_mode_for_failure_type(failure_type: str) -> str:
    """repair | regenerate | cannot_plan_hint — Python does not build the plan."""
    if failure_type in {FAILURE_TYPE_STRUCTURAL, FAILURE_TYPE_ALIAS}:
        return "repair"
    if failure_type == FAILURE_TYPE_AMBIGUITY:
        return "cannot_plan_hint"
    if failure_type == FAILURE_TYPE_RESULT:
        return "regenerate"
    return "regenerate"
