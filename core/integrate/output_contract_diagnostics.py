"""Phase 31: read-only output-contract diagnostics (no outcome mutation).

Observes Planner-declared final_output_requirements and plan structure.
Does NOT infer missing user intent, mutate plans, or change validators.
Does NOT use scenario names, domain keywords, or golden answers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from core.integrate.integration_plan_types import IntegrationPlan


@dataclass
class OutputContractDiagnostics:
    """Deterministic observations of declared output contract vs plan structure."""

    declared_grain: str | None = None
    declared_required_columns: list[str] = field(default_factory=list)
    one_row_represents: str | None = None
    has_final_output_requirements: bool = False
    selected_operations: list[str] = field(default_factory=list)
    aggregate_group_by: list[str] = field(default_factory=list)
    aggregate_metric_aliases: list[str] = field(default_factory=list)
    join_key_columns: list[str] = field(default_factory=list)
    select_columns: list[str] = field(default_factory=list)
    source_ids_referenced: list[str] = field(default_factory=list)
    final_output: str | None = None
    # Structural consistency (declared vs plan) — not user-intent inference
    required_columns_subset_of_group_by_or_metrics: bool | None = None
    required_columns_not_in_aggregate_outputs: list[str] = field(default_factory=list)
    group_by_not_in_required_columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_plan_parts(
    plan: IntegrationPlan | dict[str, Any] | None,
) -> tuple[str, list[Any], dict[str, Any] | None, str | None]:
    if plan is None:
        return "", [], None, None
    if isinstance(plan, IntegrationPlan):
        req = plan.final_output_requirements
        req_d = req.to_dict() if req and not req.is_empty else None
        return plan.status, list(plan.steps), req_d, plan.final_output
    status = str(plan.get("status") or "")
    steps = list(plan.get("steps") or [])
    req = plan.get("final_output_requirements")
    req_d = dict(req) if isinstance(req, dict) and req else None
    return status, steps, req_d, plan.get("final_output")


def observe_output_contract(
    plan: IntegrationPlan | dict[str, Any] | None,
    *,
    known_source_ids: list[str] | None = None,
) -> OutputContractDiagnostics:
    """Build output-contract diagnostics from an IntegrationPlan."""
    d = OutputContractDiagnostics()
    status, steps, req, final_output = _as_plan_parts(plan)
    d.final_output = str(final_output) if final_output else None

    if not plan or status == "cannot_plan":
        d.notes.append("no_planned_output_contract")
        return d

    if req:
        d.has_final_output_requirements = True
        d.declared_grain = str(req.get("grain") or "") or None
        d.declared_required_columns = [str(c) for c in (req.get("required_columns") or [])]
        orr = req.get("one_row_represents")
        d.one_row_represents = str(orr) if orr else None
    else:
        d.notes.append("final_output_requirements_absent")

    ops: list[str] = []
    group_by: list[str] = []
    aliases: list[str] = []
    join_keys: list[str] = []
    select_cols: list[str] = []
    refs: set[str] = set()
    known = set(known_source_ids or [])

    for s in steps:
        if isinstance(s, dict):
            op = str(s.get("op") or "")
            params = s.get("params") or {}
            inputs = list(s.get("inputs") or [])
        else:
            op = str(s.op)
            params = s.params or {}
            inputs = list(s.inputs or [])
        ops.append(op)
        for inp in inputs:
            name = str(inp)
            if not known or name in known:
                # Always record leaf-looking ids; filter later if known provided
                refs.add(name)
        if op == "aggregate":
            group_by.extend(str(x) for x in (params.get("group_by") or []))
            for m in params.get("metrics") or []:
                if isinstance(m, dict):
                    alias = m.get("alias") or m.get("column")
                    if alias:
                        aliases.append(str(alias))
        elif op == "join":
            join_keys.extend(str(x) for x in (params.get("left_keys") or []))
            join_keys.extend(str(x) for x in (params.get("right_keys") or []))
        elif op == "select_columns":
            select_cols.extend(str(x) for x in (params.get("columns") or []))

    d.selected_operations = ops
    d.aggregate_group_by = list(dict.fromkeys(group_by))
    d.aggregate_metric_aliases = list(dict.fromkeys(aliases))
    d.join_key_columns = list(dict.fromkeys(join_keys))
    d.select_columns = list(dict.fromkeys(select_cols))
    if known:
        d.source_ids_referenced = sorted(refs & known)
    else:
        # Without known sources, keep only ids that never appear as step outputs
        outputs = set()
        for s in steps:
            out = s.get("output") if isinstance(s, dict) else s.output
            if out:
                outputs.add(str(out))
        d.source_ids_referenced = sorted(refs - outputs)

    if d.declared_required_columns and (d.aggregate_group_by or d.aggregate_metric_aliases):
        allowed = set(d.aggregate_group_by) | set(d.aggregate_metric_aliases)
        missing = [c for c in d.declared_required_columns if c not in allowed]
        d.required_columns_not_in_aggregate_outputs = missing
        d.required_columns_subset_of_group_by_or_metrics = len(missing) == 0
        d.group_by_not_in_required_columns = [
            c for c in d.aggregate_group_by if c not in set(d.declared_required_columns)
        ]

    return d
