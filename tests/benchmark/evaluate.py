"""Level 1–4 evaluation helpers (semantic/structural, not exact plan JSON)."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from tests.benchmark.schema import BenchmarkCase, ExpectedSpec


def observe_route(meta: dict[str, Any] | None, *, system: bool = False) -> str:
    if system:
        return "system"
    meta = meta or {}
    agg = meta.get("aggregation") or {}
    op = str(agg.get("operation") or "")
    if op == "analysis_plan":
        return "analysis_plan"
    if op in {"legacy_simple_groupby_fallback", "groupby"}:
        return "legacy_fallback"
    if op in {"value_match", "list_seed"}:
        return "retrieval"
    if meta.get("source") == "pandasai" or op == "pandasai":
        return "pandasai"
    if meta.get("chart_path"):
        return "chart"
    return "unknown"


def plan_ops(plan_dict: dict[str, Any] | None) -> set[str]:
    if not plan_dict:
        return set()
    ops: set[str] = set()
    for step in plan_dict.get("steps") or []:
        if isinstance(step, dict):
            op = str(step.get("op") or step.get("operation") or "").strip()
            if op:
                ops.add(op)
    raw_op = str(plan_dict.get("operation") or "").strip()
    if raw_op:
        ops.add(raw_op)
    return ops


def plan_columns_used(plan_dict: dict[str, Any] | None) -> set[str]:
    """Collect column-like string leaves from a plan (best-effort)."""
    found: set[str] = set()

    def _walk(obj: Any, key_hint: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                _walk(v, str(k))
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, key_hint)
        elif isinstance(obj, str):
            hint = key_hint.lower()
            if any(
                tok in hint
                for tok in (
                    "column",
                    "group",
                    "metric",
                    "numerator",
                    "denominator",
                    "value",
                    "left",
                    "right",
                    "by",
                    "x_",
                    "y_",
                    "label",
                )
            ):
                if obj and not obj.startswith("_"):
                    found.add(obj)

    if not plan_dict:
        return found
    _walk(plan_dict)
    return found


def eval_routing(expected: ExpectedSpec, observed: str) -> tuple[bool | None, str]:
    if not expected.route:
        return None, "route not specified"
    want = expected.route
    aliases = {
        "analysis_plan": {"analysis_plan"},
        "system": {"system"},
        "retrieval": {"retrieval", "value_match", "list_seed"},
        "legacy_fallback": {"legacy_fallback", "groupby", "legacy_simple_groupby_fallback"},
        "pandasai": {"pandasai"},
        "fallback": {"legacy_fallback", "pandasai", "groupby", "legacy_simple_groupby_fallback"},
        "failure_safe": {"unknown", "pandasai", "legacy_fallback", "analysis_plan", "system"},
    }
    ok = observed in aliases.get(want, {want})
    if want == "failure_safe":
        # any non-crash route counts; caller marks crash separately
        ok = observed != "crash"
    return ok, f"expected={want} observed={observed}"


def eval_plan(expected: ExpectedSpec, plan_dict: dict[str, Any] | None) -> tuple[bool | None, str, str]:
    """Return (ok, detail, failure_category_hint)."""
    if not expected.required_operations and not expected.expected_columns and not expected.forbidden_columns:
        if expected.forbidden_operations:
            ops = plan_ops(plan_dict)
            bad = [op for op in expected.forbidden_operations if op in ops]
            if bad:
                return False, f"forbidden ops present: {bad}", "wrong_operation"
            return True, "forbidden ops absent", "none"
        return None, "plan checks not specified", "none"

    if plan_dict is None:
        return False, "no plan", "plan_generation_error"

    ops = plan_ops(plan_dict)
    missing = [op for op in expected.required_operations if op not in ops]
    # allow some aliases
    aliases = {
        "aggregate": {"aggregate", "ratio_of_aggregates", "compare_groups"},
        "filter_rows": {"filter_rows", "find_items"},
        "filter_vs_mean": {"filter_vs_mean", "rate_vs_mean"},
        "correlation": {"correlation"},
        "sort": {"sort"},
        "limit": {"limit"},
        "top_per_group": {"top_per_group", "top_n_per_group"},
        "compare_groups": {"compare_groups", "group_comparison"},
        "ratio_of_aggregates": {"ratio_of_aggregates", "group_comparison", "rate_vs_mean"},
    }
    still_missing = []
    for op in missing:
        alts = aliases.get(op, {op})
        if not alts.intersection(ops):
            still_missing.append(op)
    if still_missing:
        return False, f"missing ops {still_missing}; have {sorted(ops)}", "wrong_operation"

    forbidden = [op for op in expected.forbidden_operations if op in ops]
    if forbidden:
        return False, f"forbidden ops {forbidden}", "wrong_operation"

    cols = plan_columns_used(plan_dict)
    exp_cols = expected.expected_columns or {}
    for role, name in exp_cols.items():
        if role in {"group_by", "metric", "numerator", "denominator", "column", "x", "y"}:
            want = str(name)
            # role key itself may encode multiple names as list
            wants = name if isinstance(name, list) else [want]
            if not any(str(w) in cols or str(w) in str(plan_dict) for w in wants):
                return False, f"expected column {role}={wants} not found in plan", "wrong_column"

    for bad in expected.forbidden_columns:
        if bad in cols:
            return False, f"forbidden column {bad} used", "wrong_column"

    return True, f"ops={sorted(ops)}", "none"


def eval_execution(
    expected: ExpectedSpec,
    result_df: pd.DataFrame | None,
) -> tuple[bool | None, str, str]:
    if not expected.expected_result:
        if result_df is None:
            return None, "no result expectation", "none"
        return True, f"rows={len(result_df)}", "none"

    if result_df is None or (isinstance(result_df, pd.DataFrame) and result_df.empty and expected.expected_result):
        # empty may be valid for some filters; only fail if keys expected
        if expected.expected_result:
            return False, "empty/missing result", "wrong_result"
        return True, "empty ok", "none"

    tol = float(expected.result_tolerance)
    exp = expected.expected_result

    # Pattern A: {label: value} mapped via first non-numeric col + first numeric col
    if all(not str(k).startswith("_") for k in exp.keys()) and not any(
        k in exp for k in ("row_count", "min_rows", "max_rows", "contains", "metric_values")
    ):
        label_cols = [
            c
            for c in result_df.columns
            if not pd.api.types.is_numeric_dtype(result_df[c])
        ]
        num_cols = [
            c
            for c in result_df.columns
            if pd.api.types.is_numeric_dtype(result_df[c])
            or pd.to_numeric(result_df[c], errors="coerce").notna().any()
        ]
        if label_cols and num_cols:
            label_col = label_cols[0]
            # prefer metric name from expected_columns
            metric = (expected.expected_columns or {}).get("metric")
            value_col = metric if metric in result_df.columns else num_cols[-1]
            mapping = {
                str(r[label_col]).strip(): float(pd.to_numeric(r[value_col], errors="coerce"))
                for _, r in result_df.iterrows()
            }
            for key, want in exp.items():
                if str(key) in {"row_count", "min_rows", "max_rows"}:
                    continue
                got = mapping.get(str(key))
                if got is None:
                    # try partial match
                    hits = [v for k, v in mapping.items() if str(key) in k or k in str(key)]
                    got = hits[0] if hits else None
                if got is None:
                    return False, f"missing label {key} in {mapping}", "wrong_result"
                if abs(float(got) - float(want)) > tol:
                    return False, f"{key}: got {got} want {want}", "wrong_result"
            return True, "label-value match", "none"

    if "row_count" in exp:
        if len(result_df) != int(exp["row_count"]):
            return False, f"row_count {len(result_df)} != {exp['row_count']}", "wrong_result"
    if "min_rows" in exp and len(result_df) < int(exp["min_rows"]):
        return False, f"min_rows fail {len(result_df)}", "wrong_result"
    if "max_rows" in exp and len(result_df) > int(exp["max_rows"]):
        return False, f"max_rows fail {len(result_df)}", "wrong_result"
    if "contains" in exp:
        blob = result_df.astype(str).to_csv(index=False)
        for token in exp["contains"]:
            if str(token) not in blob:
                return False, f"missing token {token}", "wrong_result"
    if "metric_values" in exp:
        for col, values in (exp["metric_values"] or {}).items():
            if col not in result_df.columns:
                return False, f"missing col {col}", "wrong_column"
            series = pd.to_numeric(result_df[col], errors="coerce")
            for want in values:
                if not any(abs(float(v) - float(want)) <= tol for v in series.dropna()):
                    return False, f"{col} missing value {want}", "wrong_result"

    return True, "result checks passed", "none"


_NUM_RE = re.compile(
    r"(?<![A-Za-z_])[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?%?"
)


def eval_interpretation_grounding(
    reply: str | None,
    result_df: pd.DataFrame | None,
) -> tuple[bool | None, str]:
    """Heuristic: numbers in reply should appear (approx) in result table."""
    if not reply or result_df is None:
        return None, "no interpretation to check"
    # Collect numbers from result
    allowed: set[str] = set()
    for col in result_df.columns:
        for val in result_df[col].tolist():
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            if isinstance(val, (int, float)):
                allowed.add(f"{val:.4g}")
                allowed.add(f"{float(val):.2f}")
                allowed.add(f"{float(val):.2%}")
                allowed.add(str(int(val)) if float(val).is_integer() else str(val))
            else:
                text = str(val)
                for m in _NUM_RE.findall(text):
                    allowed.add(m.replace(",", ""))

    invented = []
    for m in _NUM_RE.findall(reply):
        raw = m.replace(",", "").replace("%", "")
        try:
            num = float(raw)
        except ValueError:
            continue
        # allow small integers that are counts/ranks often restated
        if abs(num) < 20 and float(num).is_integer():
            continue
        ok = False
        for a in allowed:
            try:
                if abs(float(a.replace("%", "")) - num) <= max(1.0, abs(num) * 0.02):
                    ok = True
                    break
            except ValueError:
                if a in m or m in a:
                    ok = True
                    break
        if not ok:
            invented.append(m)
    if invented:
        return False, f"possible invented numbers: {invented[:5]}"
    return True, "no obvious invented numbers"


def classify_failure(
    *,
    case: BenchmarkCase,
    routing_ok: bool | None,
    plan_ok: bool | None,
    exec_ok: bool | None,
    interp_ok: bool | None,
    plan_hint: str,
    exec_hint: str,
    crashed: bool,
    route_observed: str,
) -> str:
    if crashed:
        return "crash"
    if case.expected.expect_safe_failure:
        return "safe_failure_ok"
    if routing_ok is False:
        return "routing_error"
    if plan_hint == "plan_validation_error" or "validation" in plan_hint:
        return "plan_validation_error"
    if plan_ok is False:
        return plan_hint if plan_hint in {
            "wrong_column",
            "wrong_operation",
            "plan_generation_error",
            "plan_validation_error",
        } else "wrong_operation"
    if exec_ok is False:
        return exec_hint if exec_hint in {
            "wrong_result",
            "wrong_column",
            "wrong_filter",
            "execution_error",
            "result_validation_error",
        } else "wrong_result"
    if interp_ok is False:
        return "interpreter_grounding_error"
    if route_observed in {"legacy_fallback", "pandasai"} and case.expected.route == "analysis_plan":
        return "fallback"
    return "none"
