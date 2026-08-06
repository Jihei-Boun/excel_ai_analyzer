"""행/열 필터·프로젝션 연산."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.schema.row_classify import (
    META_COLUMNS_SET,
    ROW_TYPE_COL,
    classify_rows,
    infer_dimension_columns,
)
from core.summary.summary_utils import cell_text
from core.io.text_normalize import normalize_text

AGGREGATE_FNS = frozenset({"sum", "mean", "min", "max", "count"})
NUMERIC_FILTER_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte"})


def ensure_row_types(
    df: pd.DataFrame,
    *,
    dimension_columns: list[str] | None = None,
    footer_labels: list[str] | tuple[str, ...] | None = None,
) -> pd.DataFrame:
    if ROW_TYPE_COL in df.columns:
        return df
    dims = dimension_columns or infer_dimension_columns(
        df.drop(columns=[c for c in META_COLUMNS_SET if c in df.columns], errors="ignore")
    )
    base = df.drop(columns=[c for c in META_COLUMNS_SET if c in df.columns], errors="ignore")
    return classify_rows(
        base,
        dimension_columns=dims,
        footer_labels=footer_labels,
    )


def apply_column_filters(
    df: pd.DataFrame,
    column_filters: list[dict[str, Any]] | None,
) -> pd.DataFrame:
    """``[{column, values}]`` 값 포함 필터. 정규화 문자열 비교."""
    if not column_filters:
        return df
    work = df
    for spec in column_filters:
        if not isinstance(spec, dict):
            continue
        column = str(spec.get("column") or "")
        values = spec.get("values") or []
        if isinstance(values, str):
            values = [values]
        values = [str(v) for v in values if str(v).strip()]
        if not column or column not in work.columns or not values:
            continue
        targets = {normalize_text(v) for v in values}
        mask = work[column].map(lambda v: normalize_text(cell_text(v)) in targets)
        work = work.loc[mask]
    return work


def apply_numeric_filters(
    df: pd.DataFrame,
    numeric_filters: list[dict[str, Any]] | None,
) -> pd.DataFrame:
    """``[{column, op, value}]`` 수치 비교 필터. op: eq/ne/gt/gte/lt/lte."""
    if not numeric_filters:
        return df
    work = df
    for spec in numeric_filters:
        if not isinstance(spec, dict):
            continue
        column = str(spec.get("column") or "")
        op = str(spec.get("op") or spec.get("operator") or "").lower().strip()
        if column not in work.columns or op not in NUMERIC_FILTER_OPS:
            continue
        try:
            threshold = float(spec.get("value"))
        except (TypeError, ValueError):
            continue
        series = pd.to_numeric(work[column], errors="coerce")
        if op == "eq":
            mask = series.fillna(threshold + 1) == threshold
        elif op == "ne":
            mask = series.fillna(threshold) != threshold
        elif op == "gt":
            mask = series.fillna(threshold) > threshold
        elif op == "gte":
            mask = series.fillna(threshold - 1) >= threshold
        elif op == "lt":
            mask = series.fillna(threshold) < threshold
        else:  # lte
            mask = series.fillna(threshold + 1) <= threshold
        work = work.loc[mask]
    return work


def project_readable_columns(
    df: pd.DataFrame,
    *,
    keep_columns: list[str] | None = None,
    preferred_labels: tuple[str, ...] | None = None,
    profile_name: str | None = None,
) -> pd.DataFrame:
    """식별·조건 확인에 필요한 열만 남긴다. keep가 없으면 라벨 열만.

    preferred_labels 기본값은 활성 프로필에서 가져온다 (일반 모드는 도메인 비목 가정 없음).
    """
    if df is None or df.empty:
        return df
    if preferred_labels is None:
        from core.profile_loader import preferred_labels_for

        preferred_labels = preferred_labels_for(
            profile_name=profile_name,
        )
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(col: str) -> None:
        if col in df.columns and col not in seen:
            ordered.append(col)
            seen.add(col)

    for col in preferred_labels:
        _add(col)
    for col in keep_columns or []:
        _add(str(col))
    if not ordered:
        return df
    return df.loc[:, ordered].copy()


