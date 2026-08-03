"""분석 계획 원자 연산 실행기."""

from __future__ import annotations

from typing import Any

import pandas as pd

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
    meta: dict[str, Any] = {"steps_run": []}

    for step in plan.steps:
        work = _run_step(work, step, plan)
        meta["steps_run"].append(step.op)

    # 최종 결과에 내부 메타가 남아 있으면 제거 (select가 명시하지 않은 경우)
    drop_meta = [c for c in META_COLUMNS if c in work.columns]
    if drop_meta:
        work = work.drop(columns=drop_meta)

    meta["criteria_note"] = plan.criteria_note
    meta["row_count"] = int(len(work))
    return work.reset_index(drop=True), meta


def _run_step(df: pd.DataFrame, step: AnalysisStep, plan: AnalysisPlan) -> pd.DataFrame:
    op = step.op
    if op == "annotate_row_types":
        dims = plan.dimension_columns or infer_dimension_columns(
            df.drop(columns=[c for c in META_COLUMNS if c in df.columns], errors="ignore")
        )
        base = df.drop(columns=[c for c in META_COLUMNS if c in df.columns], errors="ignore")
        return classify_rows(base, dimension_columns=dims)

    if op == "filter_rows":
        return _filter_rows(df, step.payload, plan)

    if op == "derive_column":
        return _derive_column(df, step.payload)

    if op == "sort":
        by = [c for c in step.payload.get("by", []) if c in df.columns]
        if not by:
            return df
        ascending = step.payload.get("ascending") or [False] * len(by)
        ascending = list(ascending)[: len(by)]
        while len(ascending) < len(by):
            ascending.append(False)
        return df.sort_values(by, ascending=ascending, kind="mergesort")

    if op == "limit":
        n = int(step.payload.get("n") or 5)
        return df.head(n)

    if op == "select_columns":
        cols = [c for c in step.payload.get("columns", []) if c in df.columns]
        if not cols:
            # 메타만 빼고 전부
            keep = [c for c in df.columns if c not in META_COLUMNS]
            return df[keep] if keep else df
        out = df[cols].copy()
        renames = step.payload.get("renames") or {}
        valid = {k: v for k, v in renames.items() if k in out.columns and v}
        if valid:
            out = out.rename(columns=valid)
        return out

    if op == "drop_columns":
        cols = [c for c in step.payload.get("columns", []) if c in df.columns]
        return df.drop(columns=cols) if cols else df

    raise ValueError(f"지원하지 않는 연산: {op!r}")


def _filter_rows(
    df: pd.DataFrame,
    payload: dict[str, Any],
    plan: AnalysisPlan,
) -> pd.DataFrame:
    work = df
    if ROW_TYPE_COL not in work.columns:
        dims = payload.get("dimension_columns") or plan.dimension_columns or infer_dimension_columns(work)
        work = classify_rows(work, dimension_columns=dims)

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
                # 지정 dimension 중 하나라도 값이 있어야 함 (보통 항목명)
                primary = present[0]
                for cand in present:
                    # 명칭성 컬럼 우선: _2 접미사나 비코드
                    if str(cand).endswith("_2") or "명" in str(cand):
                        primary = cand
                        break
                has_label = work[primary].map(lambda v: bool(cell_text(v)))
                mask &= has_label

    return work.loc[mask].copy()


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

    if kind in {"diff", "abs_diff", "ratio"}:
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
        else:  # ratio
            denom = right.replace(0, pd.NA)
            work[name] = left / denom
        return work

    raise ValueError(f"지원하지 않는 derive 식: {kind!r}")
