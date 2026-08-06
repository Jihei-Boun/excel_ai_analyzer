"""AnalysisPlan step sanitize."""

from __future__ import annotations

from typing import Any

from core.analysis.analysis_plan_types import (
    ROW_TYPES,
    SUPPORTED_ANALYSIS_OPS,
    SUPPORTED_DERIVE_EXPRS,
    AnalysisStep,
)

def _sanitize_step(item: dict[str, Any], columns: set[str]) -> AnalysisStep | None:
    op = str(item.get("op") or item.get("operation") or "").strip()
    if op not in SUPPORTED_ANALYSIS_OPS:
        return None

    if op == "annotate_row_types":
        return AnalysisStep(op, {})

    if op == "filter_rows":
        include = item.get("include_row_types") or item.get("include") or ["detail"]
        if isinstance(include, str):
            include = [include]
        include = [str(x) for x in include if str(x) in ROW_TYPES]
        if not include:
            include = ["detail"]
        dim_cols = [
            str(c)
            for c in (item.get("dimension_columns") or [])
            if str(c) in columns
        ]
        column_filters = _sanitize_column_filters(item, columns)
        # 단일 column/values 단축형
        if not column_filters and item.get("column") and item.get("values") is not None:
            column_filters = _sanitize_column_filters(
                {
                    "column_filters": [
                        {"column": item.get("column"), "values": item.get("values")}
                    ]
                },
                columns,
            )
        numeric_filters = _sanitize_numeric_filters(item, columns)
        return AnalysisStep(
            op,
            {
                "include_row_types": include,
                "drop_blank_dimensions": bool(item.get("drop_blank_dimensions", True)),
                "exclude_uncertain": bool(item.get("exclude_uncertain", False)),
                "dimension_columns": dim_cols,
                "column_filters": column_filters,
                "numeric_filters": numeric_filters,
            },
        )

    if op == "select_columns":
        cols = item.get("columns")
        if cols is None and isinstance(item.get("column_renames"), dict):
            cols = list(item["column_renames"].keys())
        if isinstance(cols, str):
            cols = [cols]
        cols = [str(c) for c in (cols or [])]
        renames = item.get("renames") or item.get("column_renames") or {}
        if not isinstance(renames, dict):
            renames = {}
        return AnalysisStep(
            op,
            {
                "columns": cols,
                "renames": {str(k): str(v) for k, v in renames.items() if str(k)},
            },
        )

    if op == "derive_column":
        name = str(item.get("name") or "").strip()
        expr = item.get("expr") or item.get("formula")
        if isinstance(expr, dict) and "type" in expr and len(expr) > 1:
            # {type: ratio, numerator, denominator} → {ratio: [num, den]}
            kind = str(expr.get("type"))
            if kind in SUPPORTED_DERIVE_EXPRS:
                if kind == "ratio":
                    expr = {
                        "ratio": [
                            str(expr.get("numerator") or ""),
                            str(expr.get("denominator") or ""),
                        ]
                    }
                elif kind == "abs":
                    expr = {"abs": [str(expr.get("column") or expr.get("operand") or "")]}
                else:
                    left = str(expr.get("left") or expr.get("numerator") or "")
                    right = str(expr.get("right") or expr.get("denominator") or "")
                    expr = {kind: [left, right]}
        if not name or not isinstance(expr, dict) or not expr:
            return None
        kind = str(next(iter(expr.keys())))
        if kind not in SUPPORTED_DERIVE_EXPRS:
            return None
        operands = expr.get(kind) or []
        if isinstance(operands, str):
            operands = [operands]
        operands = [str(x) for x in operands]
        if kind == "abs" and len(operands) != 1:
            return None
        if kind == "sign_label" and len(operands) not in {1, 2}:
            return None
        if kind in {"diff", "abs_diff", "ratio", "percent_ratio"} and len(operands) != 2:
            return None
        # sign_label 1피연산자는 직전 파생열(차이)일 수 있어 columns 소속을 강제하지 않는다.
        if kind == "sign_label" and len(operands) == 1:
            return AnalysisStep(op, {"name": name, "expr": {kind: operands}})
        if any(opnd not in columns for opnd in operands):
            return None
        return AnalysisStep(op, {"name": name, "expr": {kind: operands}})

    if op == "sort":
        by = item.get("by") or item.get("columns") or []
        if isinstance(by, str):
            by = [by]
        by = [str(x) for x in by]
        if not by:
            return None
        ascending = item.get("ascending", False)
        if isinstance(ascending, bool):
            ascending = [ascending] * len(by)
        elif isinstance(ascending, list):
            ascending = [bool(x) for x in ascending]
            while len(ascending) < len(by):
                ascending.append(False)
            ascending = ascending[: len(by)]
        else:
            ascending = [bool(ascending)] * len(by)
        return AnalysisStep(op, {"by": by, "ascending": ascending})

    if op == "limit":
        try:
            n = max(1, min(100, int(item.get("n") or item.get("limit") or 5)))
        except (TypeError, ValueError):
            n = 5
        return AnalysisStep(op, {"n": n})

    if op == "drop_columns":
        cols = item.get("columns") or []
        if isinstance(cols, str):
            cols = [cols]
        return AnalysisStep(op, {"columns": [str(c) for c in cols]})

    if op == "aggregate":
        group_by = item.get("group_by") or item.get("group_column") or []
        if isinstance(group_by, str):
            group_by = [group_by]
        group_by = [str(c) for c in group_by if str(c) in columns]
        if not group_by:
            return None
        metrics = item.get("metrics") or []
        if not isinstance(metrics, list) or not metrics:
            return None
        include_groups = item.get("include_groups") or item.get("groups") or []
        if isinstance(include_groups, str):
            include_groups = [include_groups]
        prefer = item.get("prefer_subtotals")
        if prefer is None:
            prefer = True
        return AnalysisStep(
            op,
            {
                "group_by": group_by,
                "metrics": metrics,
                "prefer_subtotals": bool(prefer),
                "include_groups": [str(g) for g in include_groups if str(g).strip()],
            },
        )

    if op == "ratio_of_aggregates":
        name = str(item.get("name") or "비율").strip() or "비율"
        formula = item.get("formula") if isinstance(item.get("formula"), dict) else {}
        numerator = str(
            item.get("numerator") or formula.get("numerator") or ""
        )
        denominator = str(
            item.get("denominator") or formula.get("denominator") or ""
        )
        if not numerator or not denominator:
            return None
        # 피연산자는 원본 또는 이전 aggregate 결과 컬럼일 수 있음 — 이름은 유지
        return AnalysisStep(
            op,
            {"name": name, "numerator": numerator, "denominator": denominator},
        )

    if op == "compare_groups":
        group_column = str(item.get("group_column") or "")
        if not group_column:
            return None
        metrics = item.get("metrics") or []
        if isinstance(metrics, str):
            metrics = [metrics]
        groups = item.get("groups") or []
        if isinstance(groups, str):
            groups = [groups]
        rate_columns = item.get("rate_columns") or []
        if isinstance(rate_columns, str):
            rate_columns = [rate_columns]
        return AnalysisStep(
            op,
            {
                "group_column": group_column,
                "metrics": [str(m) for m in metrics],
                "groups": [str(g) for g in groups if str(g).strip()],
                "rate_columns": [str(c) for c in rate_columns],
            },
        )

    if op == "distribution_summary":
        denominator = str(
            item.get("denominator_column") or item.get("budget_column") or ""
        )
        numerator = str(
            item.get("numerator_column") or item.get("executed_column") or ""
        )
        if not denominator or not numerator:
            return None
        return AnalysisStep(
            op,
            {
                "group_column": str(item.get("group_column") or "") or None,
                "item_column": str(item.get("item_column") or "") or None,
                "denominator_column": denominator,
                "numerator_column": numerator,
                # 하위 호환 별칭
                "budget_column": denominator,
                "executed_column": numerator,
                "group_value": str(item.get("group_value") or "") or None,
                "zero_threshold": float(item.get("zero_threshold") or 0.0),
            },
        )

    if op == "correlation":
        x_col = str(item.get("x_column") or item.get("column_x") or "")
        y_col = str(item.get("y_column") or item.get("column_y") or "")
        value_cols = item.get("value_columns") or item.get("columns") or []
        if isinstance(value_cols, str):
            value_cols = [value_cols]
        if (not x_col or x_col not in columns) and isinstance(value_cols, list):
            valid = [str(c) for c in value_cols if str(c) in columns]
            if len(valid) >= 2:
                x_col, y_col = valid[0], valid[1]
        if x_col not in columns or y_col not in columns:
            return None
        label_col = str(item.get("label_column") or item.get("item_column") or "")
        methods = item.get("methods") or ["pearson", "spearman"]
        if isinstance(methods, str):
            methods = [methods]
        return AnalysisStep(
            op,
            {
                "x_column": x_col,
                "y_column": y_col,
                "label_column": label_col if label_col in columns else None,
                "methods": [str(m) for m in methods if str(m).strip()]
                or ["pearson", "spearman"],
            },
        )

    if op == "filter_vs_mean":
        # 파생열(집행률 등)일 수 있어 원본 columns 소속은 강제하지 않는다.
        column = str(item.get("column") or item.get("name") or "").strip()
        if not column:
            return None
        relation = str(item.get("relation") or item.get("compare") or "below")
        return AnalysisStep(op, {"column": column, "relation": relation})

    if op == "top_per_group":
        group_col = str(
            item.get("group_column") or item.get("group_by") or item.get("by") or ""
        ).strip()
        value_col = str(
            item.get("value_column")
            or item.get("metric")
            or item.get("metric_column")
            or ""
        ).strip()
        if group_col not in columns or value_col not in columns:
            return None
        try:
            n = int(item.get("n") or item.get("top_n") or item.get("limit") or 1)
        except (TypeError, ValueError):
            n = 1
        ascending = item.get("ascending", False)
        if isinstance(ascending, list):
            ascending = bool(ascending[0]) if ascending else False
        else:
            ascending = bool(ascending)
        return AnalysisStep(
            op,
            {
                "group_column": group_col,
                "value_column": value_col,
                "n": max(1, min(50, n)),
                "ascending": ascending,
            },
        )

    return None


def _sanitize_column_filters(
    item: dict[str, Any],
    columns: set[str],
) -> list[dict[str, Any]]:
    raw = item.get("column_filters") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for spec in raw:
        if not isinstance(spec, dict):
            continue
        col = str(spec.get("column") or "")
        if col not in columns:
            continue
        values = spec.get("values") or []
        if isinstance(values, str):
            values = [values]
        values = [str(v) for v in values if str(v).strip()]
        if values:
            out.append({"column": col, "values": values})
    return out


def _sanitize_numeric_filters(
    item: dict[str, Any],
    columns: set[str],
) -> list[dict[str, Any]]:
    from core.analysis.analysis_ops import NUMERIC_FILTER_OPS

    raw = item.get("numeric_filters") or item.get("conditions") or []
    if not isinstance(raw, list):
        return []
    op_aliases = {
        "==": "eq",
        "=": "eq",
        "!=": "ne",
        "<>": "ne",
        ">": "gt",
        ">=": "gte",
        "<": "lt",
        "<=": "lte",
        "eq": "eq",
        "ne": "ne",
        "gt": "gt",
        "gte": "gte",
        "lt": "lt",
        "lte": "lte",
        "equal": "eq",
        "greater": "gt",
        "less": "lt",
    }
    out: list[dict[str, Any]] = []
    for spec in raw:
        if not isinstance(spec, dict):
            continue
        col = str(spec.get("column") or "")
        if col not in columns:
            continue
        raw_op = str(spec.get("op") or spec.get("operator") or "").strip().lower()
        op = op_aliases.get(raw_op, raw_op)
        if op not in NUMERIC_FILTER_OPS:
            continue
        try:
            value = float(spec.get("value"))
        except (TypeError, ValueError):
            continue
        out.append({"column": col, "op": op, "value": value})
    return out
