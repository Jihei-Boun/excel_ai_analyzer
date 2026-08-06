"""분석 계획 원자 연산 실행기."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.analysis_ops import (
    aggregate_groups,
    apply_column_filters,
    apply_numeric_filters,
    compare_groups,
    correlation_of_columns,
    distribution_summary,
    ensure_row_types,
    filter_vs_mean,
    ratio_of_columns,
    top_per_group,
)
from core.analysis_plan_types import META_COLUMNS, AnalysisPlan, AnalysisStep
from core.row_classify import (
    ROW_CONF_COL,
    ROW_REASONS_COL,
    ROW_TYPE_COL,
    classify_rows,
    infer_dimension_columns,
)
from core.summary_utils import cell_text


def execute_analysis_plan(
    df: pd.DataFrame,
    plan: AnalysisPlan,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """원자 step만 수행한다. 질문 해석은 하지 않는다."""
    if df is None or df.empty:
        raise ValueError("실행할 데이터가 비어 있습니다.")

    work = df.copy()
    meta: dict[str, Any] = {"steps_run": [], "warnings": []}

    for step in plan.steps:
        work, step_meta = _run_step(work, step, plan)
        meta["steps_run"].append(step.op)
        for key, value in step_meta.items():
            if key == "warnings" and isinstance(value, list):
                meta["warnings"].extend(value)
            elif key in {
                "comparison",
                "structured",
                "aggregate_sources",
                "distribution",
                "correlation",
                "vs_mean",
                "top_per_group",
            }:
                meta[key] = value
            elif key == "aggregate_warnings" and isinstance(value, list):
                meta["warnings"].extend(value)

    # 최종 결과에 내부 메타가 남아 있으면 제거 (select가 명시하지 않은 경우)
    drop_meta = [c for c in META_COLUMNS if c in work.columns]
    if drop_meta:
        work = work.drop(columns=drop_meta)

    meta["criteria_note"] = plan.criteria_note
    meta["row_count"] = int(len(work))
    return work.reset_index(drop=True), meta


def _run_step(
    df: pd.DataFrame,
    step: AnalysisStep,
    plan: AnalysisPlan,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    op = step.op
    if op == "annotate_row_types":
        dims = plan.dimension_columns or infer_dimension_columns(
            df.drop(columns=[c for c in META_COLUMNS if c in df.columns], errors="ignore")
        )
        base = df.drop(columns=[c for c in META_COLUMNS if c in df.columns], errors="ignore")
        return (
            classify_rows(
                base,
                dimension_columns=dims,
                footer_labels=plan.footer_labels,
            ),
            {},
        )

    if op == "filter_rows":
        return _filter_rows(df, step.payload, plan), {}

    if op == "derive_column":
        return _derive_column(df, step.payload), {}

    if op == "sort":
        by = [c for c in step.payload.get("by", []) if c in df.columns]
        if not by:
            return df, {}
        ascending = step.payload.get("ascending") or [False] * len(by)
        ascending = list(ascending)[: len(by)]
        while len(ascending) < len(by):
            ascending.append(False)
        return df.sort_values(by, ascending=ascending, kind="mergesort"), {}

    if op == "limit":
        n = int(step.payload.get("n") or 5)
        return df.head(n), {}

    if op == "select_columns":
        cols = [c for c in step.payload.get("columns", []) if c in df.columns]
        if not cols:
            keep = [c for c in df.columns if c not in META_COLUMNS]
            return (df[keep] if keep else df), {}
        out = df[cols].copy()
        renames = step.payload.get("renames") or {}
        valid = {k: v for k, v in renames.items() if k in out.columns and v}
        if valid:
            out = out.rename(columns=valid)
        return out, {}

    if op == "drop_columns":
        cols = [c for c in step.payload.get("columns", []) if c in df.columns]
        return (df.drop(columns=cols) if cols else df), {}

    if op == "aggregate":
        result, agg_meta = aggregate_groups(
            df,
            group_by=list(step.payload.get("group_by") or []),
            metrics=list(step.payload.get("metrics") or []),
            prefer_subtotals=bool(step.payload.get("prefer_subtotals", True)),
            include_groups=list(step.payload.get("include_groups") or []) or None,
            dimension_columns=plan.dimension_columns or None,
        )
        return result, agg_meta

    if op == "ratio_of_aggregates":
        return (
            ratio_of_columns(
                df,
                name=str(step.payload.get("name") or "비율"),
                numerator=str(step.payload.get("numerator") or ""),
                denominator=str(step.payload.get("denominator") or ""),
            ),
            {},
        )

    if op == "compare_groups":
        result, cmp_meta = compare_groups(
            df,
            group_column=str(step.payload.get("group_column") or ""),
            metrics=list(step.payload.get("metrics") or []),
            groups=list(step.payload.get("groups") or []) or None,
            rate_columns=list(step.payload.get("rate_columns") or []) or None,
        )
        return result, cmp_meta

    if op == "distribution_summary":
        result, dist_meta = distribution_summary(
            df,
            group_column=step.payload.get("group_column"),
            item_column=step.payload.get("item_column"),
            denominator_column=str(
                step.payload.get("denominator_column")
                or step.payload.get("budget_column")
                or ""
            ),
            numerator_column=str(
                step.payload.get("numerator_column")
                or step.payload.get("executed_column")
                or ""
            ),
            group_value=step.payload.get("group_value"),
            zero_threshold=float(step.payload.get("zero_threshold") or 0.0),
            profile_name=step.payload.get("profile_name"),
        )
        return result, {"distribution": dist_meta}

    if op == "correlation":
        result, corr_meta = correlation_of_columns(
            df,
            x_column=str(step.payload.get("x_column") or ""),
            y_column=str(step.payload.get("y_column") or ""),
            label_column=step.payload.get("label_column"),
            methods=list(step.payload.get("methods") or []) or None,
        )
        warnings = list(corr_meta.get("warnings") or [])
        return result, {"correlation": corr_meta, "warnings": warnings}

    if op == "filter_vs_mean":
        result, vs_meta = filter_vs_mean(
            df,
            column=str(step.payload.get("column") or ""),
            relation=str(step.payload.get("relation") or "below"),
        )
        return result, {"vs_mean": vs_meta}

    if op == "top_per_group":
        result, top_meta = top_per_group(
            df,
            group_column=str(step.payload.get("group_column") or ""),
            value_column=str(step.payload.get("value_column") or ""),
            n=int(step.payload.get("n") or 1),
            ascending=bool(step.payload.get("ascending", False)),
        )
        return result, {"top_per_group": top_meta}

    raise ValueError(f"지원하지 않는 연산: {op!r}")


def _filter_rows(
    df: pd.DataFrame,
    payload: dict[str, Any],
    plan: AnalysisPlan,
) -> pd.DataFrame:
    work = ensure_row_types(
        df,
        dimension_columns=plan.dimension_columns or None,
        footer_labels=plan.footer_labels,
    )

    include = set(payload.get("include_row_types") or ["detail"])
    mask = work[ROW_TYPE_COL].astype(str).isin(include)

    if payload.get("exclude_uncertain"):
        mask &= work[ROW_CONF_COL].astype(str) != "low"

    if payload.get("drop_blank_dimensions", True):
        dims = payload.get("dimension_columns") or plan.dimension_columns
        if not dims:
            dims = infer_dimension_columns(
                work.drop(
                    columns=[c for c in (ROW_TYPE_COL, ROW_CONF_COL, ROW_REASONS_COL) if c in work.columns],
                    errors="ignore",
                )
            )
        if dims:
            present = [c for c in dims if c in work.columns]
            if present:
                primary = present[0]
                for cand in present:
                    if str(cand).endswith("_2") or "명" in str(cand):
                        primary = cand
                        break
                has_label = work[primary].map(lambda v: bool(cell_text(v)))
                mask &= has_label

    filtered = work.loc[mask].copy()
    filtered = apply_column_filters(filtered, payload.get("column_filters"))
    return apply_numeric_filters(filtered, payload.get("numeric_filters"))


def _derive_column(df: pd.DataFrame, payload: dict[str, Any]) -> pd.DataFrame:
    name = str(payload.get("name") or "").strip()
    expr = payload.get("expr") or {}
    if not name or not isinstance(expr, dict) or not expr:
        raise ValueError("derive_column에 name/expr이 필요합니다.")

    kind = str(next(iter(expr.keys())))
    operands = expr.get(kind) or []
    if isinstance(operands, str):
        operands = [operands]
    operands = [str(x) for x in operands]

    work = df.copy()
    if kind == "abs":
        col = operands[0]
        if col not in work.columns:
            raise ValueError(f"derive 피연산자 없음: {col}")
        work[name] = pd.to_numeric(work[col], errors="coerce").abs()
        return work

    if kind == "sign_label":
        if len(operands) == 1:
            series = pd.to_numeric(work[operands[0]], errors="coerce")
        elif len(operands) == 2:
            left_c, right_c = operands
            if left_c not in work.columns or right_c not in work.columns:
                raise ValueError(f"derive 피연산자 없음: {operands}")
            series = pd.to_numeric(work[left_c], errors="coerce") - pd.to_numeric(
                work[right_c], errors="coerce"
            )
        else:
            raise ValueError("sign_label는 피연산자 1~2개가 필요합니다.")
        labels = pd.Series("동일", index=work.index, dtype=object)
        labels = labels.mask(series > 0, "증가").mask(series < 0, "감소")
        labels = labels.mask(series.isna(), pd.NA)
        work[name] = labels
        return work

    if kind in {"diff", "abs_diff", "ratio", "percent_ratio"}:
        if len(operands) != 2:
            raise ValueError(f"{kind}는 피연산자 2개가 필요합니다.")
        left_c, right_c = operands
        if left_c not in work.columns or right_c not in work.columns:
            raise ValueError(f"derive 피연산자 없음: {operands}")
        left = pd.to_numeric(work[left_c], errors="coerce")
        right = pd.to_numeric(work[right_c], errors="coerce")
        if kind == "diff":
            work[name] = left - right
        elif kind == "abs_diff":
            work[name] = (left - right).abs()
        elif kind == "percent_ratio":
            denom = right.replace(0, pd.NA)
            work[name] = (left / denom) * 100.0
        else:
            denom = right.replace(0, pd.NA)
            work[name] = left / denom
        return work

    raise ValueError(f"지원하지 않는 derive 식: {kind!r}")
