"""Phase 39U — Deterministic relational-state observation for plan validation.

Observes uniqueness/cardinality on the frames produced by *declared* operations.
Does not infer filters, join keys, partitions, or repair plans.

All state is caller-local. Source DataFrames are copied; never mutated.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.integrate.integration_plan_types import IntegrationStep

# Same thresholds as integration_plan_validate._v_join (do not redefine policy).
UNIQUE_THRESHOLD = 0.98
MANY_THRESHOLD = 0.95


def copy_source_frames(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Request-local copies. Never alias caller frames."""
    return {str(k): v.copy(deep=True) for k, v in frames.items()}


def uniqueness_on_keys(df: pd.DataFrame, keys: list[str]) -> float | None:
    """Uniqueness of the declared key tuple. None if it cannot be established.

    Empty frames: None (do not invent vacuous safety).
    Formula matches relationship_profile._column_uniqueness / _composite_uniqueness
    for non-empty frames (nunique/groupby dropna=False).
    """
    if df is None or not keys:
        return None
    missing = [k for k in keys if k not in df.columns]
    if missing:
        return None
    n = int(len(df))
    if n == 0:
        return None
    if len(keys) == 1:
        return float(df[keys[0]].nunique(dropna=False) / n)
    distinct = int(df.groupby(list(keys), dropna=False).ngroups)
    return float(distinct / n)


def cardinality_from_uniqueness(left_u: float, right_u: float) -> str:
    """Reuse existing join-cardinality policy. Does not pick keys."""
    if left_u >= UNIQUE_THRESHOLD and right_u >= UNIQUE_THRESHOLD:
        return "one_to_one"
    if left_u >= UNIQUE_THRESHOLD and right_u < UNIQUE_THRESHOLD:
        return "one_to_many"
    if right_u >= UNIQUE_THRESHOLD and left_u < UNIQUE_THRESHOLD:
        return "many_to_one"
    if left_u < MANY_THRESHOLD and right_u < MANY_THRESHOLD:
        return "many_to_many"
    return "unknown"


def apply_declared_step(
    step: IntegrationStep,
    registry: dict[str, pd.DataFrame],
) -> pd.DataFrame | None:
    """Apply one already-declared op to local copies. None if not determinable."""
    try:
        inputs = [registry[name] for name in step.inputs]
    except KeyError:
        return None
    try:
        from core.integrate.integration_execute import (
            _op_aggregate,
            _op_filter,
            _op_join,
            _op_rename,
            _op_select,
            _op_union,
        )

        if step.op == "filter_rows":
            out, _, _ = _op_filter(step, inputs[0])
        elif step.op == "rename_columns":
            out, _, _ = _op_rename(step, inputs[0])
        elif step.op == "select_columns":
            out, _, _ = _op_select(step, inputs[0])
        elif step.op == "aggregate":
            out, _, _ = _op_aggregate(step, inputs[0])
        elif step.op == "union_rows":
            out, _, _ = _op_union(step, inputs)
        elif step.op == "join":
            out, _, _ = _op_join(step, inputs)
        else:
            return None
        return out
    except Exception:
        return None


def overlay_meta_from_frame(meta: Any, df: pd.DataFrame) -> None:
    """Copy observational uniqueness/null/row_count from a preview frame onto meta.

    Does not add/remove columns or invent keys.
    """
    meta.row_count = int(len(df))
    for name, col in list(meta.columns.items()):
        if name not in df.columns:
            continue
        series = df[name]
        n = int(len(series))
        col.uniqueness_ratio = float(series.nunique(dropna=False) / n) if n else 0.0
        col.null_ratio = float(series.isna().mean()) if n else 0.0
        col.distinct_count = int(series.nunique(dropna=True))
