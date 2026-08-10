"""AnalysisPlan step sanitize."""

from __future__ import annotations

from typing import Any

from core.analysis.analysis_plan_types import (
    ROW_TYPES,
    SUPPORTED_ANALYSIS_OPS,
    SUPPORTED_DERIVE_EXPRS,
    AnalysisStep,
)


def _resolve_column(name: object, columns: set[str]) -> str | None:
    """정확한 컬럼명 또는 canonicalize/match-key로 실제 스키마 컬럼에 매핑.

    dirty Excel의 ``' 상품 명 '`` ↔ ``상품_명`` 같은 범용 정규화 차이를 흡수한다.
    특정 dataset 컬럼명 hardcoding 없음.
    """
    raw = str(name or "").strip()
    if not raw:
        return None
    if raw in columns:
        return raw
    from core.io.normalize import canonicalize_column_name, column_match_key

    canon = canonicalize_column_name(raw)
    if canon in columns:
        return canon
    key = column_match_key(raw)
    matches = [c for c in columns if column_match_key(c) == key]
    if len(matches) == 1:
        return matches[0]
    # underscore/space-insensitive exact canon among columns
    matches = [c for c in columns if canonicalize_column_name(c) == canon]
    if len(matches) == 1:
        return matches[0]
    return None


def _resolve_columns(names: list[str], columns: set[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        resolved = _resolve_column(name, columns)
        if resolved and resolved not in seen:
            out.append(resolved)
            seen.add(resolved)
    return out


def _resolve_output_column_alias(name: object, columns: set[str]) -> str | None:
    """aggregate 이후 LLM이 만든 ``매출_합계`` / ``amount_sum`` 별칭을 원 컬럼명으로 되돌린다.

    Executor contract: aggregate는 metric column 이름을 바꾸지 않는다.
    의미 변경이 아니라 출력 naming 정합만 맞춘다.
    """
    raw = str(name or "").strip()
    if not raw:
        return None
    direct = _resolve_column(raw, columns)
    if direct:
        return direct
    from core.analysis.analysis_plan_compile import _metric_alias_candidates

    for cand in _metric_alias_candidates(raw):
        resolved = _resolve_column(cand, columns)
        if resolved:
            return resolved
        if cand in columns:
            return cand
    return None


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
        dim_cols = _resolve_columns(
            [str(c) for c in (item.get("dimension_columns") or [])],
            columns,
        )
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
        # column_filters values like ">0" / "<안전재고" → numeric_filters
        kept_cf: list[dict[str, Any]] = []
        for cf in column_filters:
            vals = cf.get("values") or []
            if len(vals) == 1:
                embedded = _parse_embedded_numeric_compare(str(vals[0]), columns)
                if embedded is not None:
                    emb_op, emb_right, emb_value = embedded
                    if emb_right and emb_right != cf["column"]:
                        numeric_filters.append(
                            {
                                "left_column": cf["column"],
                                "column": cf["column"],
                                "op": emb_op,
                                "right_column": emb_right,
                            }
                        )
                        continue
                    if emb_value is not None:
                        numeric_filters.append(
                            {
                                "column": cf["column"],
                                "op": emb_op,
                                "value": emb_value,
                            }
                        )
                        continue
            kept_cf.append(cf)
        column_filters = kept_cf
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
        resolved_cols: list[str] = []
        for c in cols or []:
            resolved = _resolve_output_column_alias(str(c), columns)
            resolved_cols.append(resolved or str(c))
        renames = item.get("renames") or item.get("column_renames") or {}
        if not isinstance(renames, dict):
            renames = {}
        return AnalysisStep(
            op,
            {
                "columns": resolved_cols,
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
        resolved_ops: list[str] = []
        for opnd in operands:
            resolved = _resolve_column(opnd, columns)
            if resolved is None:
                return None
            resolved_ops.append(resolved)
        return AnalysisStep(op, {"name": name, "expr": {kind: resolved_ops}})

    if op == "sort":
        by = item.get("by") or item.get("columns") or []
        if isinstance(by, str):
            by = [by]
        by = [_resolve_output_column_alias(str(x), columns) for x in by]
        by = [x for x in by if x]
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
        group_by = _resolve_columns([str(c) for c in group_by], columns)
        if not group_by:
            return None
        from core.analysis.analysis_plan_compile import (
            _metric_alias_candidates,
            _resolve_metric_column,
        )

        metrics = item.get("metrics") or []
        if isinstance(metrics, dict):
            if "column" in metrics or "fn" in metrics or "agg" in metrics:
                metrics = [metrics]
            else:
                metrics = [{k: v} for k, v in metrics.items()]
        if not isinstance(metrics, list) or not metrics:
            return None
        resolved_metrics: list[Any] = []
        for metric in metrics:
            col = ""
            fn = ""
            if isinstance(metric, str):
                # string-only metric is invalid without fn (Phase 8)
                continue
            if isinstance(metric, dict):
                if "column" in metric or "name" in metric:
                    col = str(metric.get("column") or metric.get("name") or "")
                    fn = str(metric.get("fn") or metric.get("agg") or "").strip()
                elif len(metric) == 1:
                    key, val = next(iter(metric.items()))
                    if str(key) not in {"column", "name", "fn", "agg"}:
                        col = str(key)
                        fn = str(val or "").strip()
            resolved = _resolve_metric_column(col, columns)
            if not resolved or not fn:
                continue
            resolved_metrics.append({"column": resolved, "fn": fn.lower()})
        if not resolved_metrics:
            return None
        include_groups = item.get("include_groups") or item.get("groups") or []
        if isinstance(include_groups, str):
            include_groups = [include_groups]
        prefer = item.get("prefer_subtotals")
        if prefer is None:
            prefer = all(str(m.get("fn")) == "sum" for m in resolved_metrics)
        return AnalysisStep(
            op,
            {
                "group_by": group_by,
                "metrics": resolved_metrics,
                "prefer_subtotals": bool(prefer),
                "include_groups": [str(g) for g in include_groups if str(g).strip()],
            },
        )

    if op == "ratio_of_aggregates":
        name = str(item.get("name") or "비율").strip() or "비율"
        formula = item.get("formula") if isinstance(item.get("formula"), dict) else {}
        numerator = _resolve_column(
            item.get("numerator") or formula.get("numerator") or "",
            columns,
        ) or str(item.get("numerator") or formula.get("numerator") or "")
        denominator = _resolve_column(
            item.get("denominator") or formula.get("denominator") or "",
            columns,
        ) or str(item.get("denominator") or formula.get("denominator") or "")
        if not numerator or not denominator:
            return None
        # 피연산자는 원본 또는 이전 aggregate 결과 컬럼일 수 있음 — 이름은 유지
        return AnalysisStep(
            op,
            {"name": name, "numerator": numerator, "denominator": denominator},
        )

    if op == "compare_groups":
        group_column = _resolve_column(item.get("group_column") or "", columns) or str(
            item.get("group_column") or ""
        )
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
        resolved_metrics = []
        for m in metrics:
            if isinstance(m, dict):
                col = str(m.get("column") or m.get("name") or "").strip()
                resolved = _resolve_output_column_alias(col, columns) if col else None
                if resolved:
                    resolved_metrics.append(resolved)
                elif col:
                    resolved_metrics.append(col)
                continue
            resolved = _resolve_output_column_alias(str(m), columns)
            if resolved:
                resolved_metrics.append(resolved)
            elif isinstance(m, str) and m.strip():
                resolved_metrics.append(m.strip())
        resolved_rates = []
        for c in rate_columns:
            resolved = _resolve_column(c, columns)
            if resolved:
                resolved_rates.append(resolved)
        return AnalysisStep(
            op,
            {
                "group_column": group_column,
                "metrics": resolved_metrics,
                "groups": [str(g) for g in groups if str(g).strip()],
                "rate_columns": resolved_rates,
            },
        )

    if op == "distribution_summary":
        denominator = _resolve_column(
            item.get("denominator_column") or item.get("budget_column") or "",
            columns,
        ) or str(item.get("denominator_column") or item.get("budget_column") or "")
        numerator = _resolve_column(
            item.get("numerator_column") or item.get("executed_column") or "",
            columns,
        ) or str(item.get("numerator_column") or item.get("executed_column") or "")
        if not denominator or not numerator:
            return None
        group_column = _resolve_column(item.get("group_column") or "", columns)
        item_column = _resolve_column(item.get("item_column") or "", columns)
        return AnalysisStep(
            op,
            {
                "group_column": group_column,
                "item_column": item_column,
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
        x_col = _resolve_column(
            item.get("x_column") or item.get("column_x") or "", columns
        ) or ""
        y_col = _resolve_column(
            item.get("y_column") or item.get("column_y") or "", columns
        ) or ""
        value_cols = item.get("value_columns") or item.get("columns") or []
        if isinstance(value_cols, str):
            value_cols = [value_cols]
        if (not x_col or not y_col) and isinstance(value_cols, list):
            valid = _resolve_columns([str(c) for c in value_cols], columns)
            if len(valid) >= 2:
                x_col, y_col = valid[0], valid[1]
        if not x_col or not y_col or x_col not in columns or y_col not in columns:
            return None
        label_col = _resolve_column(
            item.get("label_column") or item.get("item_column") or "", columns
        )
        methods = item.get("methods") or ["pearson", "spearman"]
        if isinstance(methods, str):
            methods = [methods]
        return AnalysisStep(
            op,
            {
                "x_column": x_col,
                "y_column": y_col,
                "label_column": label_col,
                "methods": [str(m) for m in methods if str(m).strip()]
                or ["pearson", "spearman"],
            },
        )

    if op == "filter_vs_mean":
        # 파생열(집행률 등)일 수 있어 원본 columns 소속은 강제하지 않는다.
        column = str(item.get("column") or item.get("name") or "").strip()
        column = _resolve_column(column, columns) or column
        if not column:
            return None
        relation = str(item.get("relation") or item.get("compare") or "below")
        return AnalysisStep(op, {"column": column, "relation": relation})

    if op == "top_per_group":
        group_col = _resolve_column(
            item.get("group_column") or item.get("group_by") or item.get("by") or "",
            columns,
        )
        value_col = _resolve_column(
            item.get("value_column")
            or item.get("metric")
            or item.get("metric_column")
            or "",
            columns,
        )
        if not group_col or not value_col:
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
        col = _resolve_column(spec.get("column") or "", columns)
        if not col:
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
        left = _resolve_column(
            spec.get("left_column")
            or spec.get("column")
            or spec.get("left")
            or "",
            columns,
        )
        right = _resolve_column(
            spec.get("right_column")
            or spec.get("other_column")
            or spec.get("right")
            or "",
            columns,
        )
        if not left:
            continue
        raw_op = str(spec.get("op") or spec.get("operator") or "").strip().lower()
        op = op_aliases.get(raw_op, raw_op)
        if op in {"==", "!=", ">", ">=", "<", "<="}:
            op = op_aliases.get(op, op)
        raw_value = spec.get("value")
        # LLM often encodes comparison inside value: ">0", ">= 10", "<안전재고"
        if isinstance(raw_value, str) and raw_value.strip():
            embedded = _parse_embedded_numeric_compare(raw_value.strip(), columns)
            if embedded is not None:
                emb_op, emb_right_col, emb_value = embedded
                if not op or op not in {"eq", "ne", "gt", "gte", "lt", "lte"}:
                    op = emb_op
                if emb_right_col and emb_right_col != left:
                    out.append(
                        {
                            "left_column": left,
                            "column": left,
                            "op": op,
                            "right_column": emb_right_col,
                        }
                    )
                    continue
                if emb_value is not None and op in {"eq", "ne", "gt", "gte", "lt", "lte"}:
                    out.append({"column": left, "op": op, "value": emb_value})
                    continue
        if op not in {"eq", "ne", "gt", "gte", "lt", "lte"} and op not in NUMERIC_FILTER_OPS:
            continue
        if right:
            out.append(
                {
                    "left_column": left,
                    "column": left,
                    "op": op,
                    "right_column": right,
                }
            )
            continue
        # LLM이 종종 column-vs-column을 value에 컬럼명 문자열로 넣는다.
        if isinstance(raw_value, str) and raw_value.strip():
            as_col = _resolve_column(raw_value, columns)
            if as_col and as_col != left:
                out.append(
                    {
                        "left_column": left,
                        "column": left,
                        "op": op,
                        "right_column": as_col,
                    }
                )
                continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        out.append({"column": left, "op": op, "value": value})
    return out


def _parse_embedded_numeric_compare(
    text: str,
    columns: set[str],
) -> tuple[str, str | None, float | None] | None:
    """Parse strings like '>0', '>= 10', '<안전재고' into (op, right_col|None, value|None)."""
    import re

    m = re.match(r"^(>=|<=|!=|<>|>|<|=|==)\s*(.+)$", text.strip())
    if not m:
        return None
    op_aliases = {
        ">": "gt",
        ">=": "gte",
        "<": "lt",
        "<=": "lte",
        "=": "eq",
        "==": "eq",
        "!=": "ne",
        "<>": "ne",
    }
    op = op_aliases.get(m.group(1), "")
    rhs = m.group(2).strip().strip("'\"")
    if not op or not rhs:
        return None
    as_col = _resolve_column(rhs, columns)
    if as_col:
        return op, as_col, None
    try:
        return op, None, float(rhs.replace(",", ""))
    except ValueError:
        return None
