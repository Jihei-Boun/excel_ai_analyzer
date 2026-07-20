"""분석 결과 후처리·리스트 표시 판별."""

from __future__ import annotations

import pandas as pd

from core.analyzer import find_mentioned_column, format_context_label
from core.pandasai_config import is_total_label

_SOURCE_COL = "출처파일"

_LIST_DISPLAY_KEYWORDS = (
    "리스트",
    "목록",
    "뽑아",
    "나열",
    "list",
)


def expects_list_display(prompt: str) -> bool:
    """단일 값 나열 형태로 보여줄 요청인지 판별한다."""
    lowered = prompt.lower()
    if not any(keyword in lowered for keyword in _LIST_DISPLAY_KEYWORDS):
        return False
    if "표로" in lowered or "표 형" in lowered:
        return False
    return True


def exclude_aggregate_rows(
    df: pd.DataFrame,
    prompt: str,
    *,
    source_col: str = _SOURCE_COL,
) -> tuple[pd.DataFrame, int]:
    """합계·소계 등 집계 행을 제거한다."""
    if df is None or df.empty:
        return df, 0

    check_columns = _aggregate_check_columns(df, prompt, source_col=source_col)
    if not check_columns:
        return df, 0

    exclude_mask = pd.Series(False, index=df.index)
    for column in check_columns:
        exclude_mask |= df[column].map(_cell_is_total_label)

    if not exclude_mask.any():
        return df, 0

    filtered = df.loc[~exclude_mask].reset_index(drop=True)
    return filtered, int(exclude_mask.sum())


def to_list_display(
    df: pd.DataFrame,
    prompt: str,
    *,
    source_col: str = _SOURCE_COL,
) -> tuple[list[str], str] | None:
    """리스트 표시용 (값 목록, 컬럼 라벨)을 추출한다."""
    if df is None or df.empty or not expects_list_display(prompt):
        return None

    column = _list_target_column(df, prompt, source_col=source_col)
    if column is None:
        return None

    values: list[str] = []
    seen: set[str] = set()
    for raw in df[column].tolist():
        text = _clean_cell_text(raw)
        if not text or _cell_is_total_label(raw):
            continue
        if text in seen:
            continue
        seen.add(text)
        values.append(text)

    if not values:
        return None

    label = format_context_label(column)
    return values, label


def _aggregate_check_columns(
    df: pd.DataFrame,
    prompt: str,
    *,
    source_col: str,
) -> list[str]:
    mentioned = find_mentioned_column(df, prompt)
    if mentioned:
        return [mentioned]

    text_columns = [
        column
        for column in df.columns
        if column != source_col and not pd.api.types.is_numeric_dtype(df[column])
    ]
    if len(text_columns) == 1:
        return text_columns
    if text_columns:
        return [text_columns[0]]
    return []


def _list_target_column(
    df: pd.DataFrame,
    prompt: str,
    *,
    source_col: str,
) -> str | None:
    mentioned = find_mentioned_column(df, prompt)
    if mentioned and mentioned in df.columns:
        return mentioned

    candidates = [
        column
        for column in df.columns
        if column != source_col and not pd.api.types.is_numeric_dtype(df[column])
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(df.columns) == 1:
        return str(df.columns[0])
    return None


def _cell_is_total_label(value: object) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return is_total_label(value)


def _clean_cell_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat", "<na>"}:
        return ""
    return text
