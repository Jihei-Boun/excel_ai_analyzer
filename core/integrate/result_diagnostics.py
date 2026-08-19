"""Phase 29: read-only result / plan diagnostics (no outcome mutation).

Observes generic, golden-independent signals for silent wrong-success analysis.
Does NOT change Plan Validator / Result Validator / Executor decisions.
Does NOT use scenario names, domain keywords, or golden answers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from core.integrate.integration_plan_types import (
    FINAL_GRAIN_COLLAPSED,
    FINAL_GRAIN_ROW_LEVEL,
    IntegrationPlan,
)


@dataclass
class ResultDiagnostics:
    """Deterministic observations derived from plan + optional execution metadata."""

    declared_grain: str | None = None
    declared_required_columns: list[str] = field(default_factory=list)
    has_one_row_represents: bool = False
    selected_operations: list[str] = field(default_factory=list)
    has_aggregate: bool = False
    has_join: bool = False
    has_union: bool = False
    has_select: bool = False
    # Candidate observability signals (flags only — not production gates)
    row_grain_with_collapsing_aggregate: bool = False
    collapsed_grain_without_aggregate: bool = False
    aggregate_present_without_declared_grain: bool = False
    final_required_columns_count: int = 0
    # Execution-side optional
    final_row_count: int | None = None
    final_column_count: int | None = None
    final_columns: list[str] = field(default_factory=list)
    declared_required_missing_in_final: list[str] = field(default_factory=list)
    source_count: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def observe_plan_diagnostics(
    plan: IntegrationPlan | dict[str, Any] | None,
    *,
    execution_meta: dict[str, Any] | None = None,
    final_columns: list[str] | None = None,
    final_shape: list[int] | None = None,
    source_count: int | None = None,
) -> ResultDiagnostics:
    """Build diagnostics from IntegrationPlan (+ optional execution observations)."""
    d = ResultDiagnostics()
    if plan is None:
        d.notes.append("no_plan")
        return d

    if isinstance(plan, IntegrationPlan):
        status = plan.status
        steps = plan.steps
        req = plan.final_output_requirements
        ops = [s.op for s in steps]
        grain = req.grain if req else None
        req_cols = list(req.required_columns) if req else []
        orr = bool(req and req.one_row_represents)
    else:
        status = str(plan.get("status") or "")
        steps = plan.get("steps") or []
        req = plan.get("final_output_requirements") or {}
        ops = [str(s.get("op")) for s in steps if isinstance(s, dict)]
        grain = req.get("grain") if isinstance(req, dict) else None
        req_cols = list(req.get("required_columns") or []) if isinstance(req, dict) else []
        orr = bool(isinstance(req, dict) and req.get("one_row_represents"))

    if status == "cannot_plan":
        d.notes.append("cannot_plan")
        d.selected_operations = []
        return d

    grain_s = str(grain).strip().lower() if grain else None
    if grain_s == "":
        grain_s = None

    d.declared_grain = grain_s
    d.declared_required_columns = [str(c) for c in req_cols]
    d.final_required_columns_count = len(d.declared_required_columns)
    d.has_one_row_represents = orr
    d.selected_operations = list(ops)
    d.has_aggregate = "aggregate" in ops
    d.has_join = "join" in ops
    d.has_union = "union_rows" in ops
    d.has_select = "select_columns" in ops
    d.source_count = source_count

    # I1 / I3 family: row-level declared grain + collapsing aggregate in plan
    if grain_s in FINAL_GRAIN_ROW_LEVEL and d.has_aggregate:
        d.row_grain_with_collapsing_aggregate = True
        d.notes.append("row_grain_with_collapsing_aggregate")

    if grain_s in FINAL_GRAIN_COLLAPSED and not d.has_aggregate:
        d.collapsed_grain_without_aggregate = True
        d.notes.append("collapsed_grain_without_aggregate")

    if d.has_aggregate and not grain_s:
        d.aggregate_present_without_declared_grain = True
        d.notes.append("aggregate_present_without_declared_grain")

    cols = list(final_columns or [])
    if not cols and execution_meta:
        cols = [str(x) for x in (execution_meta.get("final_columns") or [])]
    shape = final_shape
    if shape is None and execution_meta:
        shape = execution_meta.get("final_shape")
    if isinstance(shape, (list, tuple)) and len(shape) >= 2:
        d.final_row_count = int(shape[0])
        d.final_column_count = int(shape[1])
    if cols:
        d.final_columns = cols
        missing = [c for c in d.declared_required_columns if c not in set(cols)]
        d.declared_required_missing_in_final = missing
        if missing:
            d.notes.append("declared_required_missing_in_final")

    return d


def observe_from_pipeline_result(pipeline: Any) -> ResultDiagnostics:
    """Convenience wrapper for IntegrationPipelineResult-like objects."""
    plan = getattr(pipeline, "plan", None)
    meta = dict(getattr(pipeline, "metadata", None) or {})
    final = getattr(pipeline, "final_output", None)
    cols = None
    shape = None
    if final is not None and hasattr(final, "columns"):
        cols = [str(c) for c in final.columns]
        shape = [int(final.shape[0]), int(final.shape[1])]
    return observe_plan_diagnostics(
        plan,
        execution_meta=meta,
        final_columns=cols,
        final_shape=shape,
        source_count=meta.get("source_count"),
    )
