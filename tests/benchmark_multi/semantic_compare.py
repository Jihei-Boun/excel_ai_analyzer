"""Benchmark-only semantic result comparison (Phase 23).

Does NOT run in production. Compares results using plan lineage /
aggregate operation identity — never string similarity of aliases.

False-pass safeguards:
- different source metric → fail
- different aggregation fn → fail
- wrong grain (detail vs aggregate) when expected_row_count / ops disagree → fail
- coincidental equal values with wrong operation identity → fail
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from tests.benchmark_multi.schema import MultiBenchmarkCase, ResultCompareSpec


@dataclass
class SemanticCompareResult:
    ok: bool
    semantic_equivalent: bool = False
    representation_only: bool = False
    structural_mismatch: bool = False
    grain_mismatch: bool = False
    true_semantic_mismatch: bool = False
    alias_only_mismatch: bool = False
    grain_match: bool | None = None
    metric_match: bool | None = None
    values_match: bool | None = None
    expected_grain: str | None = None
    actual_grain: str | None = None
    expected_semantic_metrics: list[dict[str, Any]] = field(default_factory=list)
    actual_semantic_metrics: list[dict[str, Any]] = field(default_factory=list)
    column_mapping: dict[str, str] = field(default_factory=dict)  # expected→actual
    reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "semantic_equivalent": self.semantic_equivalent,
            "representation_only": self.representation_only,
            "structural_mismatch": self.structural_mismatch,
            "grain_mismatch": self.grain_mismatch,
            "true_semantic_mismatch": self.true_semantic_mismatch,
            "alias_only_mismatch": self.alias_only_mismatch,
            "grain_match": self.grain_match,
            "metric_match": self.metric_match,
            "values_match": self.values_match,
            "expected_grain": self.expected_grain,
            "actual_grain": self.actual_grain,
            "expected_semantic_metrics": self.expected_semantic_metrics,
            "actual_semantic_metrics": self.actual_semantic_metrics,
            "column_mapping": self.column_mapping,
            "reasons": self.reasons,
            "details": self.details,
        }


def extract_aggregate_metrics(plan_dict: dict[str, Any] | None) -> list[dict[str, str]]:
    """Return [{source_column, aggregation, alias}, ...] from plan aggregates."""
    out: list[dict[str, str]] = []
    if not plan_dict:
        return out
    for step in plan_dict.get("steps") or []:
        if not isinstance(step, dict) or step.get("op") != "aggregate":
            continue
        for m in (step.get("params") or {}).get("metrics") or []:
            if not isinstance(m, dict):
                continue
            col = str(m.get("column") or "").strip()
            fn = str(m.get("function") or m.get("fn") or "").strip().lower()
            alias = str(m.get("alias") or col).strip()
            if col and fn:
                out.append(
                    {
                        "source_column": col,
                        "aggregation": fn,
                        "alias": alias,
                    }
                )
    return out


def extract_final_group_by(plan_dict: dict[str, Any] | None) -> list[str]:
    if not plan_dict:
        return []
    steps = [s for s in (plan_dict.get("steps") or []) if isinstance(s, dict)]
    final = str(plan_dict.get("final_output") or "")
    # Prefer aggregate that produces final_output; else last aggregate
    for step in reversed(steps):
        if step.get("op") != "aggregate":
            continue
        if final and str(step.get("output") or "") == final:
            return [str(x) for x in ((step.get("params") or {}).get("group_by") or [])]
    for step in reversed(steps):
        if step.get("op") == "aggregate":
            return [str(x) for x in ((step.get("params") or {}).get("group_by") or [])]
    return []


def infer_actual_grain(plan_dict: dict[str, Any] | None, df: pd.DataFrame | None) -> str:
    ops = [
        str(s.get("op"))
        for s in ((plan_dict or {}).get("steps") or [])
        if isinstance(s, dict)
    ]
    if "aggregate" in ops:
        gb = extract_final_group_by(plan_dict)
        return "summary" if not gb else "group"
    if df is not None and len(df.columns) > 0:
        return "detail"
    return "unknown"


def infer_expected_grain(case: MultiBenchmarkCase) -> str:
    spec = case.expected.result
    if getattr(spec, "expected_grain", None):
        return str(spec.expected_grain)
    req = set(case.expected.required_operations)
    if "aggregate" in req:
        return "group"
    if req & {"join", "union_rows", "rename_columns", "select_columns", "filter_rows"}:
        return "detail"
    return "unknown"


def expected_metrics_from_case(case: MultiBenchmarkCase) -> list[dict[str, str]]:
    """Benchmark expected metrics — from schema or fixed_plan (evaluation only)."""
    spec = case.expected.result
    raw = getattr(spec, "expected_metrics", None) or []
    if raw:
        out = []
        for m in raw:
            if not isinstance(m, dict):
                continue
            out.append(
                {
                    "source_column": str(m.get("source_column") or m.get("column") or "").strip(),
                    "aggregation": str(
                        m.get("aggregation") or m.get("function") or m.get("fn") or ""
                    )
                    .strip()
                    .lower(),
                    "alias": str(m.get("alias") or "").strip(),
                }
            )
        return [m for m in out if m["source_column"] and m["aggregation"]]
    # Infer from fixed_plan (golden plan), not from live plan
    return extract_aggregate_metrics(case.fixed_plan)


def metric_key(m: dict[str, str]) -> tuple[str, str]:
    return (m["source_column"], m["aggregation"])


def map_expected_metric_to_actual_column(
    expected: dict[str, str],
    actual_metrics: list[dict[str, str]],
    df: pd.DataFrame,
) -> str | None:
    """Map by (source_column, aggregation) identity — not alias string similarity."""
    want = metric_key(expected)
    candidates = [m for m in actual_metrics if metric_key(m) == want]
    for m in candidates:
        alias = m["alias"]
        if alias in df.columns:
            return alias
    # Also accept exact expected alias if present
    if expected.get("alias") and expected["alias"] in df.columns:
        # Still require that actual plan declares same identity for that alias
        for m in actual_metrics:
            if m["alias"] == expected["alias"] and metric_key(m) == want:
                return expected["alias"]
    return None


def compare_semantic_result(
    case: MultiBenchmarkCase,
    *,
    plan_dict: dict[str, Any] | None,
    final_df: Any,
) -> SemanticCompareResult:
    """Semantic L4 comparison for benchmark overall_ok.

    overall_ok may treat semantic_equivalent as pass even when presentation
    aliases differ. Safety / status gates remain outside this function.
    """
    result = SemanticCompareResult(ok=False)
    if not isinstance(final_df, pd.DataFrame):
        result.true_semantic_mismatch = True
        result.reasons.append("missing_final_df")
        return result

    df = final_df
    spec: ResultCompareSpec = case.expected.result
    result.expected_grain = infer_expected_grain(case)
    result.actual_grain = infer_actual_grain(plan_dict, df)
    result.grain_match = _grain_compatible(
        case, plan_dict, df, result.expected_grain, result.actual_grain
    )

    expected_metrics = expected_metrics_from_case(case)
    actual_metrics = extract_aggregate_metrics(plan_dict)
    result.expected_semantic_metrics = expected_metrics
    result.actual_semantic_metrics = actual_metrics

    # --- Grain gate ---
    if result.grain_match is False:
        result.grain_mismatch = True
        result.reasons.append("grain_mismatch")
        result.details["expected_grain"] = result.expected_grain
        result.details["actual_grain"] = result.actual_grain
        result.details["expected_row_count"] = spec.expected_row_count
        result.details["actual_row_count"] = int(len(df))
        return result

    # --- Metric identity (when aggregates expected or present) ---
    column_mapping: dict[str, str] = {}
    if expected_metrics:
        if not actual_metrics:
            result.true_semantic_mismatch = True
            result.metric_match = False
            result.reasons.append("expected_metrics_but_no_aggregate_in_plan")
            return result
        actual_keys = {metric_key(m) for m in actual_metrics}
        expected_keys = {metric_key(m) for m in expected_metrics}
        # Actual may include extras; required expected metrics must be covered
        if not expected_keys.issubset(actual_keys):
            result.true_semantic_mismatch = True
            result.metric_match = False
            result.reasons.append("metric_identity_mismatch")
            result.details["expected_keys"] = sorted(expected_keys)
            result.details["actual_keys"] = sorted(actual_keys)
            return result
        result.metric_match = True
        for em in expected_metrics:
            mapped = map_expected_metric_to_actual_column(em, actual_metrics, df)
            if not mapped:
                result.true_semantic_mismatch = True
                result.metric_match = False
                result.reasons.append("metric_output_column_missing")
                result.details["missing_metric"] = em
                return result
            if em.get("alias"):
                column_mapping[em["alias"]] = mapped
            # Also map common required_columns aliases
            column_mapping.setdefault(em.get("alias") or mapped, mapped)

    # Map required metric-like columns via fixed_plan aliases
    for em in expected_metrics:
        alias = em.get("alias") or ""
        if alias:
            mapped = map_expected_metric_to_actual_column(em, actual_metrics, df)
            if mapped:
                column_mapping[alias] = mapped

    # Also map from fixed_plan aliases even if expected_metrics empty but required_columns list aliases
    for m in extract_aggregate_metrics(case.fixed_plan):
        mapped = map_expected_metric_to_actual_column(m, actual_metrics, df) if actual_metrics else None
        if mapped and m.get("alias"):
            column_mapping[m["alias"]] = mapped

    result.column_mapping = dict(column_mapping)

    # --- Structural columns (non-metric required) ---
    metric_alias_names = {
        m.get("alias") for m in expected_metrics if m.get("alias")
    } | {m.get("alias") for m in extract_aggregate_metrics(case.fixed_plan) if m.get("alias")}
    structural_required = [c for c in spec.required_columns if c not in metric_alias_names]
    missing_structural = [c for c in structural_required if c not in df.columns]
    if missing_structural:
        result.structural_mismatch = True
        result.reasons.append("missing_structural_columns")
        result.details["missing_structural_columns"] = missing_structural
        return result

    # Metric required columns: must resolve via mapping or exact name
    for c in spec.required_columns:
        if c in metric_alias_names:
            actual_name = column_mapping.get(c, c)
            if actual_name not in df.columns:
                result.true_semantic_mismatch = True
                result.reasons.append("unmapped_metric_column")
                result.details["column"] = c
                return result
            if actual_name != c:
                result.alias_only_mismatch = True

    # --- Row count (grain already checked; still enforce when specified) ---
    if spec.expected_row_count is not None and int(len(df)) != int(spec.expected_row_count):
        # Allow only if grain said compatible — still fail row count for detail/group
        result.true_semantic_mismatch = True
        result.reasons.append("row_count_mismatch")
        result.details["row_count"] = int(len(df))
        result.details["expected_row_count"] = spec.expected_row_count
        return result

    # --- Value compare via lineage-mapped value column ---
    if spec.expected_result and spec.key_column and spec.value_column:
        key_col = spec.key_column
        if key_col not in df.columns:
            result.structural_mismatch = True
            result.reasons.append("key_column_missing")
            return result
        value_col = column_mapping.get(spec.value_column, spec.value_column)
        if value_col not in df.columns:
            # Try metric identity map for value_column
            for em in expected_metrics:
                if em.get("alias") == spec.value_column or not em.get("alias"):
                    mapped = map_expected_metric_to_actual_column(em, actual_metrics, df)
                    if mapped:
                        value_col = mapped
                        break
            # If value_column is a known fixed-plan alias
            if value_col not in df.columns:
                for m in extract_aggregate_metrics(case.fixed_plan):
                    if m.get("alias") == spec.value_column:
                        mapped = map_expected_metric_to_actual_column(m, actual_metrics, df)
                        if mapped:
                            value_col = mapped
                        break
        if value_col not in df.columns:
            result.true_semantic_mismatch = True
            result.reasons.append("value_column_unresolved")
            return result
        if value_col != spec.value_column:
            result.alias_only_mismatch = True
            result.column_mapping[spec.value_column] = value_col

        work = df
        if spec.sort_by:
            cols = [c for c in spec.sort_by if c in work.columns]
            if cols:
                work = work.sort_values(cols).reset_index(drop=True)

        got = {
            str(k): float(v) if isinstance(v, (int, float, np.floating)) else v
            for k, v in zip(work[key_col], work[value_col])
        }
        for k, exp in spec.expected_result.items():
            if str(k) not in got:
                result.true_semantic_mismatch = True
                result.values_match = False
                result.reasons.append("missing_result_key")
                result.details.setdefault("missing_keys", []).append(str(k))
                return result
            actual = got[str(k)]
            if isinstance(exp, (int, float)) and isinstance(actual, (int, float, np.floating)):
                if not np.isclose(float(actual), float(exp), rtol=spec.rtol, atol=spec.atol):
                    result.true_semantic_mismatch = True
                    result.values_match = False
                    result.reasons.append("value_mismatch")
                    result.details.setdefault("value_mismatches", {})[str(k)] = {
                        "expected": exp,
                        "actual": float(actual),
                    }
                    return result
            elif actual != exp:
                result.true_semantic_mismatch = True
                result.values_match = False
                result.reasons.append("value_mismatch")
                return result
        result.values_match = True
        result.details["observed_result"] = got
        result.details["value_column_used"] = value_col

    # Success path
    result.ok = True
    result.semantic_equivalent = True
    if result.alias_only_mismatch or any(
        k != v for k, v in column_mapping.items() if k in spec.required_columns
    ):
        result.representation_only = True
        result.alias_only_mismatch = True
    return result


def _grain_compatible(
    case: MultiBenchmarkCase,
    plan_dict: dict[str, Any] | None,
    df: pd.DataFrame,
    expected_grain: str,
    actual_grain: str,
) -> bool:
    req = set(case.expected.required_operations)
    ops = {
        str(s.get("op"))
        for s in ((plan_dict or {}).get("steps") or [])
        if isinstance(s, dict)
    }
    # Forbidden aggregate when detail required and aggregate not required
    if "aggregate" not in req and "aggregate" in ops:
        return False
    if "aggregate" in req and "aggregate" not in ops:
        return False
    spec = case.expected.result
    if spec.expected_row_count is not None:
        if int(len(df)) != int(spec.expected_row_count):
            # Detail vs collapsed summary
            if expected_grain == "detail" and actual_grain in {"group", "summary"}:
                return False
            if expected_grain in {"group", "summary"} and actual_grain == "detail":
                return False
            if expected_grain == "detail" and int(len(df)) != int(spec.expected_row_count):
                return False
    if expected_grain == "detail" and actual_grain in {"group", "summary"}:
        return False
    if expected_grain in {"group", "summary"} and actual_grain == "detail":
        return False
    return True
