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


# Phase 39B: minimal observable role vocabulary (opaque side_id; no domain enums).
OUTPUT_ROLE_NAMES = frozenset({"entity_key", "comparison_side"})


@dataclass
class OutputRole:
    """Planner-declared observable role binding (Phase 39B).

    Python checks structure only (role name, columns present, distinct side_ids).
    Does not interpret side_id meanings (A/B are opaque).
    """

    role: str
    columns: list[str] = field(default_factory=list)
    side_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "role": self.role,
            "columns": list(self.columns),
        }
        if self.side_id is not None:
            out["side_id"] = self.side_id
        return out


@dataclass
class FinalOutputRequirements:
    """Planner-declared final intent (optional, Phase 24–26).

    LLM decides grain/fields. Python only checks Plan consistency —
    never fills these from user keywords or benchmark goldens.

    Phase 26: optional ``one_row_represents`` is a short Planner self-check
    phrase (what one final row means). Validator does not interpret it
    semantically — it is stored for observability / retry context only.
    identity_columns intentionally not added (probe: failures are wrong grain
    declaration / projection, not identity-vs-required ambiguity).

    Phase 39B: optional ``output_roles`` binds declared columns to minimal
    observable roles (entity_key / comparison_side). Optional and backward
    compatible; absence must not fail parsing.
    """

    grain: str | None = None  # detail | entity | group | summary
    required_columns: list[str] = field(default_factory=list)
    one_row_represents: str | None = None
    output_roles: list[OutputRole] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.grain:
            out["grain"] = self.grain
        if self.required_columns:
            out["required_columns"] = list(self.required_columns)
        if self.one_row_represents:
            out["one_row_represents"] = self.one_row_represents
        if self.output_roles:
            out["output_roles"] = [r.to_dict() for r in self.output_roles]
        return out

    @property
    def is_empty(self) -> bool:
        return (
            not self.grain
            and not self.required_columns
            and not self.one_row_represents
            and not self.output_roles
        )


FINAL_GRAIN_VALUES = frozenset({"detail", "entity", "group", "summary"})
FINAL_GRAIN_ROW_LEVEL = frozenset({"detail", "entity"})
FINAL_GRAIN_COLLAPSED = frozenset({"group", "summary"})


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
    final_output_requirements: FinalOutputRequirements | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
            "final_output": self.final_output,
            "reason": self.reason,
            "ambiguities": list(self.ambiguities),
            "notes": list(self.notes),
            "meta": dict(self.meta),
        }
        if self.final_output_requirements and not self.final_output_requirements.is_empty:
            payload["final_output_requirements"] = self.final_output_requirements.to_dict()
        return payload


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
            final_output_requirements=None,
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
        final_output_requirements=_parse_final_output_requirements(data),
    )


def canonical_integration_plan_signature(plan: IntegrationPlan | dict[str, Any]) -> str:
    """Stable signature for duplicate-plan / retry diversity."""
    if isinstance(plan, IntegrationPlan):
        payload = plan.to_dict()
    else:
        payload = dict(plan)
    slim = {
        "status": payload.get("status"),
        "final_output": payload.get("final_output"),
        "final_output_requirements": payload.get("final_output_requirements"),
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


# Observability / retry diversity only — never used to synthesize a plan.
INTEGRATION_FAMILY_LABELS: dict[str, str] = {
    "join_only": "single join without further composition",
    "union_only": "union without aggregation",
    "union_then_aggregate": "union followed by aggregation",
    "join_then_aggregate": "join followed by aggregation",
    "filter_union_aggregate": "filter(s) then union then aggregate",
    "filter_then_union": "filter(s) then union",
    "multi_join_chain": "multiple joins (chain)",
    "multi_join_then_aggregate": "multiple joins then aggregate",
    "rename_then_union": "rename then union",
    "aggregate_only": "aggregate without multi-source combine",
    "select_only": "column selection only",
    "cannot_plan": "cannot_plan",
    "other": "other integration composition",
}


def integration_operation_family_signature(
    plan: IntegrationPlan | dict[str, Any] | None,
) -> str:
    """Classify integration strategy family for retry diversity / observability.

    Does NOT prescribe or generate plans. Domain-neutral op-sequence patterns only.
    """
    if plan is None:
        return "other"
    if isinstance(plan, IntegrationPlan):
        status = plan.status
        ops = [s.op for s in plan.steps]
    else:
        status = str(plan.get("status") or "")
        ops = [
            str(s.get("op") or "").strip().lower()
            for s in (plan.get("steps") or [])
            if isinstance(s, dict)
        ]
        ops = [o for o in ops if o]

    if status == "cannot_plan" or not ops:
        return "cannot_plan" if status == "cannot_plan" else "other"

    has = set(ops)
    n_join = sum(1 for o in ops if o == "join")
    n_union = sum(1 for o in ops if o == "union_rows")
    n_filter = sum(1 for o in ops if o == "filter_rows")
    n_agg = sum(1 for o in ops if o == "aggregate")
    n_rename = sum(1 for o in ops if o == "rename_columns")

    if n_filter and n_union and n_agg:
        return "filter_union_aggregate"
    if n_filter and n_union and not n_agg:
        return "filter_then_union"
    if n_join >= 2 and n_agg:
        return "multi_join_then_aggregate"
    if n_join >= 2:
        return "multi_join_chain"
    if n_join == 1 and n_agg:
        return "join_then_aggregate"
    if n_join == 1 and not n_union and not n_agg:
        return "join_only"
    if n_union and n_agg:
        return "union_then_aggregate"
    if n_union and not n_agg:
        return "union_only"
    if n_rename and n_union:
        return "rename_then_union"
    if n_agg and not n_join and not n_union:
        return "aggregate_only"
    if has <= {"select_columns"}:
        return "select_only"
    return "other"


def integration_operation_family_label(family: str | None) -> str:
    if not family:
        return INTEGRATION_FAMILY_LABELS["other"]
    return INTEGRATION_FAMILY_LABELS.get(family, family)


def repeated_integration_family_feedback(
    family: str | None,
) -> list[str]:
    """Evidence-only retry feedback — never prescribe keys or ops."""
    label = integration_operation_family_label(family)
    return [
        "Code: repeated_integration_family",
        "The same integration strategy family was already rejected.",
        f"Previous rejected family: {label}",
        "Use a materially different integration strategy if supported by the evidence, "
        "or return status=cannot_plan if ambiguity remains unresolved.",
    ]


def final_contract_failure_family(codes: list[str] | None) -> str | None:
    """Classify final-output contract failures for retry diversity (observability)."""
    codes_s = {str(c) for c in (codes or [])}
    if codes_s & {
        "join_key_dropped_in_final_projection",
        "final_required_field_missing",
        "required_field_permanently_lost",
    }:
        return "projection_failure_family"
    if codes_s & {"final_grain_contradiction", "invalid_final_grain"}:
        return "grain_failure_family"
    if codes_s & {"required_field_not_materializable"}:
        return "field_survival_failure_family"
    if codes_s & {
        "final_required_column_missing",
    }:
        return "final_requirement_family"
    return None


def repeated_final_contract_family_feedback(family: str | None) -> list[str]:
    return [
        "Code: repeated_final_contract_failure",
        "Previous attempts failed for the same final-output contract reason.",
        f"Previous rejected final-contract family: {family or 'final_requirement_family'}",
        "Use a materially different planning approach that preserves the declared "
        "grain and fields. Do not invent specific operations or column names.",
    ]


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
            # Structural materialize: always attach resolved alias so Planner,
            # Validator, and Executor share one name (no semantic rename).
            from core.integrate.integration_contracts import materialize_aggregate_metric

            item = materialize_aggregate_metric(item)
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


def _parse_final_output_requirements(data: dict[str, Any]) -> FinalOutputRequirements | None:
    """Optional Planner declaration — structural normalize only."""
    raw = data.get("final_output_requirements")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise IntegrationPlanParseError("final_output_requirements must be an object")
    grain_raw = raw.get("grain")
    grain = str(grain_raw).strip().lower() if grain_raw is not None else None
    if grain == "":
        grain = None
    if grain is not None and grain not in FINAL_GRAIN_VALUES:
        raise IntegrationPlanParseError(
            f"final_output_requirements.grain must be one of "
            f"{sorted(FINAL_GRAIN_VALUES)}, got {grain!r}"
        )
    cols_raw = raw.get("required_columns") or raw.get("required_fields") or []
    if cols_raw is None:
        cols_raw = []
    if not isinstance(cols_raw, list):
        raise IntegrationPlanParseError(
            "final_output_requirements.required_columns must be a list"
        )
    cols = [str(x).strip() for x in cols_raw if str(x).strip()]
    one_raw = raw.get("one_row_represents")
    one = str(one_raw).strip() if one_raw is not None else None
    if one == "":
        one = None
    roles = _parse_output_roles(raw.get("output_roles"))
    req = FinalOutputRequirements(
        grain=grain,
        required_columns=cols,
        one_row_represents=one,
        output_roles=roles,
    )
    return None if req.is_empty else req


def _parse_output_roles(raw: Any) -> list[OutputRole]:
    """Structural parse only — no semantic interpretation of side_id."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise IntegrationPlanParseError(
            "final_output_requirements.output_roles must be a list when present"
        )
    roles: list[OutputRole] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise IntegrationPlanParseError(
                f"final_output_requirements.output_roles[{i}] must be an object"
            )
        role = str(item.get("role") or "").strip()
        if not role:
            raise IntegrationPlanParseError(
                f"final_output_requirements.output_roles[{i}].role is required"
            )
        if role not in OUTPUT_ROLE_NAMES:
            raise IntegrationPlanParseError(
                f"final_output_requirements.output_roles[{i}].role {role!r} "
                f"unsupported; allowed={sorted(OUTPUT_ROLE_NAMES)}"
            )
        cols_raw = item.get("columns")
        if not isinstance(cols_raw, list) or not cols_raw:
            raise IntegrationPlanParseError(
                f"final_output_requirements.output_roles[{i}].columns "
                "must be a non-empty list"
            )
        columns = [str(c).strip() for c in cols_raw if str(c).strip()]
        if not columns:
            raise IntegrationPlanParseError(
                f"final_output_requirements.output_roles[{i}].columns empty"
            )
        side_raw = item.get("side_id")
        side_id = str(side_raw).strip() if side_raw is not None else None
        if side_id == "":
            side_id = None
        if role == "comparison_side" and not side_id:
            raise IntegrationPlanParseError(
                f"final_output_requirements.output_roles[{i}] "
                "comparison_side requires side_id"
            )
        if role == "entity_key" and side_id is not None:
            raise IntegrationPlanParseError(
                f"final_output_requirements.output_roles[{i}] "
                "entity_key must not set side_id"
            )
        roles.append(OutputRole(role=role, columns=columns, side_id=side_id))
    return roles


def _str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    return [str(value)]
