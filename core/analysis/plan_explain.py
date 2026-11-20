"""One Korean line that says how a result was computed, read off the executed plan.

It restates the plan's steps and nothing else: no judgement about whether the
plan fits the question. A user who sees "생산량 합계와 단위원가 합계" under a
question about total cost can tell the answer is wrong without opening the plan.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.analysis.analysis_plan_types import AnalysisPlan, AnalysisStep
from core.schema.row_classify import ROW_TYPE_COL

_FN = {
    "sum": "합계",
    "mean": "평균",
    "avg": "평균",
    "count": "개수",
    "size": "개수",
    "nunique": "고유 개수",
    "min": "최솟값",
    "max": "최댓값",
    "median": "중앙값",
}
_CMP = {
    "eq": "=", "==": "=", "ne": "≠", "!=": "≠",
    "gt": ">", ">": ">", "gte": "≥", ">=": "≥",
    "lt": "<", "<": "<", "lte": "≤", "<=": "≤",
}
_ROW_TYPES = {"subtotal": "소계", "total": "합계", "footer": "주석"}
_MAX_VALUES = 2


def explain_plan(plan: AnalysisPlan, source: pd.DataFrame | None = None) -> str:
    """Steps joined with arrows, e.g. "소계 2행·합계 1행 제외 → 지점별 매출 합계".

    ``source`` is the classified table the plan ran on. Row types are named only
    when the table really has such rows, with their count; without ``source``
    the row-type part is left out.
    """
    counts = _row_type_counts(source)
    parts: list[str] = []
    for step in plan.steps:
        parts.extend(p for p in _explain_step(step, counts) if p)
        if step.op == "filter_rows":
            counts = {}  # later filters see rows the first one already dropped
    return " → ".join(parts)


def _row_type_counts(source: pd.DataFrame | None) -> dict[str, int]:
    if source is None or ROW_TYPE_COL not in source.columns:
        return {}
    return {str(k): int(v) for k, v in source[ROW_TYPE_COL].astype(str).value_counts().items()}


def _names(values: Any, limit: int | None = None) -> str:
    if isinstance(values, str):
        values = [values]
    items = [str(v) for v in values or [] if str(v)]
    if limit is not None and len(items) > limit:
        return "·".join(items[:limit]) + f" 외 {len(items) - limit}개"
    return "·".join(items)


def _explain_step(step: AnalysisStep, counts: dict[str, int]) -> list[str]:
    p = step.payload or {}
    op = step.op
    if op == "filter_rows":
        return _explain_filter(p, counts)
    if op == "aggregate":
        return [_explain_aggregate(p)]
    if op == "derive_column":
        return [_explain_derive(p)]
    if op == "ratio_of_aggregates":
        return [f"{p.get('name') or '비율'} = {p.get('numerator')} ÷ {p.get('denominator')}"]
    if op == "sort":
        by = p.get("by") or []
        by = [by] if isinstance(by, str) else list(by)
        asc = p.get("ascending", False)
        first = asc[0] if isinstance(asc, list) and asc else asc
        return [f"{_names(by)} {'작은' if first else '큰'} 순 정렬"] if by else []
    if op == "limit":
        return [f"위에서 {p.get('n') or 5}개"]
    if op == "compare_groups":
        groups = _names(p.get("groups"))
        return [f"{p.get('group_column')}별 {_names(p.get('metrics'))} 비교" + (f" ({groups})" if groups else "")]
    if op == "distribution_summary":
        return [f"{p.get('group_column') or ''}별 분포 요약".strip()]
    if op == "correlation":
        return [f"{p.get('x_column')}와 {p.get('y_column')}의 상관관계"]
    if op == "filter_vs_mean":
        relation = "평균보다 낮은" if str(p.get("relation") or "below").startswith("below") else "평균보다 높은"
        return [f"{p.get('column')}이 {relation} 행"]
    if op == "top_per_group":
        order = "작은" if p.get("ascending") else "큰"
        return [f"{p.get('group_column')}마다 {p.get('value_column')}이 가장 {order} {p.get('n') or 1}개"]
    return []


def _explain_filter(p: dict[str, Any], counts: dict[str, int]) -> list[str]:
    out: list[str] = []
    include = set(p.get("include_row_types") or ["detail"])
    dropped = [
        f"{label} {counts[kind]}행"
        for kind, label in _ROW_TYPES.items()
        if kind not in include and counts.get(kind)
    ]
    if dropped:
        out.append(f"{'·'.join(dropped)} 제외")
    for spec in p.get("column_filters") or []:
        if isinstance(spec, dict) and spec.get("column"):
            out.append(f"{spec['column']}이 {_names(spec.get('values'), _MAX_VALUES)}인 행만")
    for spec in p.get("numeric_filters") or []:
        if not isinstance(spec, dict):
            continue
        left = spec.get("left_column") or spec.get("column") or spec.get("left")
        right = spec.get("right_column") or spec.get("other_column") or spec.get("right")
        sign = _CMP.get(str(spec.get("op") or spec.get("operator") or "").lower().strip(), "?")
        target = right if right else spec.get("value")
        if left:
            out.append(f"{left} {sign} {target}인 행만")
    return out


def _explain_aggregate(p: dict[str, Any]) -> str:
    metrics = []
    for m in p.get("metrics") or []:
        if isinstance(m, dict):
            fn = _FN.get(str(m.get("fn") or m.get("agg") or "sum"), str(m.get("fn") or "합계"))
            metrics.append(f"{m.get('column') or m.get('name')} {fn}")
        elif isinstance(m, str):
            metrics.append(f"{m} 합계")
    what = ", ".join(metrics)
    group_by = _names(p.get("group_by"))
    only = _names(p.get("include_groups"), _MAX_VALUES)
    scope = f"{group_by}별" if group_by else "전체 행의"
    if only:
        scope += f"({only}만)"
    return f"{scope} {what}"


def _explain_derive(p: dict[str, Any]) -> str:
    name = str(p.get("name") or "")
    expr = p.get("expr") or {}
    if not isinstance(expr, dict) or not expr:
        return ""
    kind = str(next(iter(expr)))
    ops = expr.get(kind) or []
    ops = [ops] if isinstance(ops, str) else [str(x) for x in ops]
    a = ops[0] if ops else "?"
    b = ops[1] if len(ops) > 1 else "?"
    formula = {
        "sum": " + ".join(ops),
        "product": " × ".join(ops),
        "mean": f"({', '.join(ops)})의 평균",
        "diff": f"{a} − {b}",
        "abs_diff": f"|{a} − {b}|",
        "abs": f"|{a}|",
        "ratio": f"{a} ÷ {b}",
        "percent_ratio": f"{a} ÷ {b} × 100",
        "sign_label": f"{a}의 증감 표시",
    }.get(kind, f"{kind}({', '.join(ops)})")
    return f"행마다 {name} = {formula}" if name else f"행마다 {formula}"
