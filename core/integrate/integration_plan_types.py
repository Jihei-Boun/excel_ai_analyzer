"""Phase 15: IntegrationPlan v1 contracts (planning only — no execution).

Structural parse/normalize only. No semantic autocomplete
(no key_candidates[0], no numeric→sum, no op rewrite to aggregate_merge).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


INTEGRATION_PLAN_STATUS = frozenset({"planned", "cannot_plan"})

# New main vocabulary — aggregate_merge is legacy sugar, not v1 main op
INTEGRATION_ATOMIC_OPS = frozenset(
    {
        "rename_columns",
        "filter_rows",
        "union_rows",
        "join",
        "aggregate",
        "select_columns",
    }
)

JOIN_HOW = frozenset({"inner", "left", "right", "outer"})

# Align with single-file AnalysisPlan metric fn vocabulary
AGGREGATE_FUNCTIONS = frozenset({"sum", "mean", "median", "min", "max", "count"})

# Align with analysis numeric filter op aliases (structural only)
FILTER_OPERATORS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte"})

_FILTER_OP_ALIASES = {
    "=": "eq",
    "==": "eq",
    "equal": "eq",
    "!=": "ne",
    "<>": "ne",
    ">": "gt",
    ">=": "gte",
    "<": "lt",
    "<=": "lte",
}


class IntegrationPlanParseError(ValueError):
    """Structural / shape contract failure (Phase 15 parser)."""


@dataclass
class IntegrationStep:
    id: str
    op: str
    inputs: list[str]
    output: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "op": self.op,
            "inputs": list(self.inputs),
            "output": self.output,
            "params": dict(self.params),
        }


@dataclass
class IntegrationPlan:
    """LLM Integration Planner output (or cannot_plan safe failure)."""

    status: str
    steps: list[IntegrationStep] = field(default_factory=list)
    final_output: str | None = None
    reason: str | None = None
    ambiguities: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
            "final_output": self.final_output,
            "reason": self.reason,
            "ambiguities": list(self.ambiguities),
            "notes": list(self.notes),
            "meta": dict(self.meta),
        }


def integration_plan_from_dict(data: Any) -> IntegrationPlan:
    """Parse LLM JSON into IntegrationPlan — structural contract only.

    Raises IntegrationPlanParseError on unsupported ops / missing required shape.
    Does NOT invent keys, metrics, or rewrite operations.
    """
    if not isinstance(data, dict):
        raise IntegrationPlanParseError("plan must be a JSON object")

    status = str(data.get("status") or "").strip().lower()
    if status not in INTEGRATION_PLAN_STATUS:
        raise IntegrationPlanParseError(
            f"status must be one of {sorted(INTEGRATION_PLAN_STATUS)}, got {status!r}"
        )

    notes = _str_list(data.get("notes"))
    ambiguities = _str_list(data.get("ambiguities"))
    reason = data.get("reason")
    reason_s = str(reason).strip() if reason is not None else None

    if status == "cannot_plan":
        steps_raw = data.get("steps") or []
        if steps_raw not in ([], None) and not (
            isinstance(steps_raw, list) and len(steps_raw) == 0
        ):
            # Allow empty only; non-empty with cannot_plan is structural inconsistency
            if isinstance(steps_raw, list) and steps_raw:
                raise IntegrationPlanParseError(
                    "cannot_plan must have empty steps[]"
                )
        return IntegrationPlan(
            status="cannot_plan",
            steps=[],
            final_output=None,
            reason=reason_s or "cannot_plan",
            ambiguities=ambiguities,
            notes=notes,
            meta={"phase": 15},
        )

    # status == planned
    steps_raw = data.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise IntegrationPlanParseError("planned status requires non-empty steps[]")

    steps: list[IntegrationStep] = []
    seen_outputs: set[str] = set()
    for i, raw in enumerate(steps_raw):
        step = _parse_step(raw, index=i)
        if step.output in seen_outputs:
            raise IntegrationPlanParseError(
                f"duplicate output id in plan: {step.output!r}"
            )
        seen_outputs.add(step.output)
        steps.append(step)

    final_output = data.get("final_output")
    if final_output is None or str(final_output).strip() == "":
        raise IntegrationPlanParseError("planned status requires final_output")
    final_s = str(final_output).strip()

    # Structural: final_output must equal some step output (dependency graph check lite)
    if final_s not in seen_outputs:
        raise IntegrationPlanParseError(
            f"final_output {final_s!r} is not produced by any step"
        )

    return IntegrationPlan(
        status="planned",
        steps=steps,
        final_output=final_s,
        reason=reason_s,
        ambiguities=ambiguities,
        notes=notes,
        meta={"phase": 15},
    )


def canonical_integration_plan_signature(plan: IntegrationPlan | dict[str, Any]) -> str:
    """Stable signature for future duplicate-plan / retry diversity (Phase 18)."""
    if isinstance(plan, IntegrationPlan):
        payload = plan.to_dict()
    else:
        payload = dict(plan)
    slim = {
        "status": payload.get("status"),
        "final_output": payload.get("final_output"),
        "steps": [
            {
                "op": s.get("op"),
                "inputs": s.get("inputs"),
                "output": s.get("output"),
                "params": s.get("params"),
            }
            for s in (payload.get("steps") or [])
            if isinstance(s, dict)
        ],
    }
    return json.dumps(_canon(slim), ensure_ascii=False, sort_keys=True)


def _canon(obj: object) -> object:
    if isinstance(obj, dict):
        return {str(k): _canon(obj[k]) for k in sorted(obj.keys(), key=str)}
    if isinstance(obj, list):
        return [_canon(x) for x in obj]
    if isinstance(obj, float):
        return round(obj, 6)
    return obj


def _parse_step(raw: Any, *, index: int) -> IntegrationStep:
    if not isinstance(raw, dict):
        raise IntegrationPlanParseError(f"step[{index}] must be an object")

    op = str(raw.get("op") or "").strip().lower()
    if op not in INTEGRATION_ATOMIC_OPS:
        # Explicit structural failure — do NOT rewrite to union/aggregate_merge
        raise IntegrationPlanParseError(
            f"unsupported op {op!r}; allowed={sorted(INTEGRATION_ATOMIC_OPS)}"
        )

    step_id = str(raw.get("id") or f"step_{index + 1}").strip()
    inputs = raw.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise IntegrationPlanParseError(f"step[{index}] requires non-empty inputs[]")
    inputs_s = [str(x).strip() for x in inputs if str(x).strip()]
    if not inputs_s:
        raise IntegrationPlanParseError(f"step[{index}] inputs[] empty after normalize")

    output = str(raw.get("output") or "").strip()
    if not output:
        raise IntegrationPlanParseError(f"step[{index}] requires output")

    params_raw = raw.get("params")
    if params_raw is None:
        params_raw = {}
    if not isinstance(params_raw, dict):
        raise IntegrationPlanParseError(f"step[{index}] params must be an object")

    params = _normalize_params(op, params_raw, index=index)
    if op == "join" and len(inputs_s) != 2:
        raise IntegrationPlanParseError(
            f"step[{index}] join requires exactly 2 inputs [left, right]"
        )
    if op == "union_rows" and len(inputs_s) < 2:
        raise IntegrationPlanParseError(
            f"step[{index}] union_rows requires at least 2 inputs"
        )
    return IntegrationStep(
        id=step_id,
        op=op,
        inputs=inputs_s,
        output=output,
        params=params,
    )


def _normalize_params(op: str, params: dict[str, Any], *, index: int) -> dict[str, Any]:
    """Op-specific structural shape checks + light enum/casing normalize."""
    if op == "rename_columns":
        mapping = params.get("mapping")
        if not isinstance(mapping, dict) or not mapping:
            raise IntegrationPlanParseError(
                f"step[{index}] rename_columns requires non-empty params.mapping"
            )
        return {
            "mapping": {str(k).strip(): str(v).strip() for k, v in mapping.items() if str(k).strip()}
        }

    if op == "filter_rows":
        conditions = params.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            raise IntegrationPlanParseError(
                f"step[{index}] filter_rows requires non-empty params.conditions"
            )
        norm_conds: list[dict[str, Any]] = []
        for j, cond in enumerate(conditions):
            if not isinstance(cond, dict):
                raise IntegrationPlanParseError(
                    f"step[{index}] conditions[{j}] must be object"
                )
            raw_op = str(cond.get("operator") or cond.get("op") or "").strip().lower()
            op_n = _FILTER_OP_ALIASES.get(raw_op, raw_op)
            if op_n not in FILTER_OPERATORS:
                raise IntegrationPlanParseError(
                    f"step[{index}] conditions[{j}] invalid operator {raw_op!r}"
                )
            # Explicit forms only — never promote value→column because the string
            # happens to match a column name (Phase 18 contract audit).
            right_col = cond.get("right_column")
            left_col = str(cond.get("left_column") or cond.get("column") or "").strip()
            if right_col is not None and str(right_col).strip():
                if not left_col:
                    raise IntegrationPlanParseError(
                        f"step[{index}] conditions[{j}] column-vs-column requires "
                        "left_column (or column) and right_column"
                    )
                rc = str(right_col).strip()
                norm_conds.append(
                    {
                        "column": left_col,
                        "left_column": left_col,
                        "operator": op_n,
                        "right_column": rc,
                    }
                )
                continue
            col = str(cond.get("column") or cond.get("left_column") or "").strip()
            if not col:
                raise IntegrationPlanParseError(
                    f"step[{index}] conditions[{j}] missing column"
                )
            if "value" not in cond:
                raise IntegrationPlanParseError(
                    f"step[{index}] conditions[{j}] missing value "
                    "(or use explicit right_column for column-vs-column)"
                )
            norm_conds.append(
                {"column": col, "operator": op_n, "value": cond.get("value")}
            )
        return {"conditions": norm_conds}

    if op == "union_rows":
        # column_policy optional structural hint only — default aligned
        policy = str(params.get("column_policy") or "aligned").strip().lower()
        if policy not in {"aligned", "intersection", "union_with_nulls"}:
            raise IntegrationPlanParseError(
                f"step[{index}] union_rows invalid column_policy {policy!r}"
            )
        return {"column_policy": policy}

    if op == "join":
        # Structural: join consumes exactly two inputs [left, right]
        # (checked at step level via inputs length in _parse_step after params)
        left_keys = params.get("left_keys")
        right_keys = params.get("right_keys")
        if not isinstance(left_keys, list) or not left_keys:
            raise IntegrationPlanParseError(
                f"step[{index}] join requires non-empty params.left_keys"
            )
        if not isinstance(right_keys, list) or not right_keys:
            raise IntegrationPlanParseError(
                f"step[{index}] join requires non-empty params.right_keys"
            )
        left_s = [str(x).strip() for x in left_keys if str(x).strip()]
        right_s = [str(x).strip() for x in right_keys if str(x).strip()]
        if not left_s or not right_s:
            raise IntegrationPlanParseError(f"step[{index}] join keys empty after normalize")
        if len(left_s) != len(right_s):
            raise IntegrationPlanParseError(
                f"step[{index}] join left_keys/right_keys length mismatch"
            )
        how = str(params.get("how") or "inner").strip().lower()
        if how not in JOIN_HOW:
            raise IntegrationPlanParseError(f"step[{index}] join invalid how {how!r}")
        return {"left_keys": left_s, "right_keys": right_s, "how": how}

    if op == "aggregate":
        group_by = params.get("group_by")
        if not isinstance(group_by, list):
            raise IntegrationPlanParseError(
                f"step[{index}] aggregate requires params.group_by list"
            )
        group_s = [str(x).strip() for x in group_by if str(x).strip()]
        metrics = params.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise IntegrationPlanParseError(
                f"step[{index}] aggregate requires non-empty params.metrics"
            )
        norm_metrics: list[dict[str, Any]] = []
        for j, m in enumerate(metrics):
            if not isinstance(m, dict):
                raise IntegrationPlanParseError(
                    f"step[{index}] metrics[{j}] must be object"
                )
            col = str(m.get("column") or "").strip()
            if not col:
                raise IntegrationPlanParseError(
                    f"step[{index}] metrics[{j}] missing column"
                )
            fn = str(m.get("function") or m.get("fn") or "").strip().lower()
            if fn == "avg":
                fn = "mean"
            if fn not in AGGREGATE_FUNCTIONS:
                raise IntegrationPlanParseError(
                    f"step[{index}] metrics[{j}] invalid function {fn!r}"
                )
            item: dict[str, Any] = {"column": col, "function": fn}
            alias = m.get("alias")
            if alias is not None and str(alias).strip():
                item["alias"] = str(alias).strip()
            norm_metrics.append(item)
        return {"group_by": group_s, "metrics": norm_metrics}

    if op == "select_columns":
        columns = params.get("columns")
        if not isinstance(columns, list) or not columns:
            raise IntegrationPlanParseError(
                f"step[{index}] select_columns requires non-empty params.columns"
            )
        cols = [str(x).strip() for x in columns if str(x).strip()]
        if not cols:
            raise IntegrationPlanParseError(
                f"step[{index}] select_columns columns empty after normalize"
            )
        return {"columns": cols}

    raise IntegrationPlanParseError(f"unhandled op {op!r}")


def _str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    return [str(value)]
