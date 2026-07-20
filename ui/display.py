"""표시용 DataFrame 헬퍼."""

from __future__ import annotations

import re

import pandas as pd

from core.excel_loader import merged_header_base

_MERGED_SUFFIX_RE = re.compile(r"^(.+)_(\d+)$")


def for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Streamlit/Arrow가 안전하게 그릴 수 있도록 타입을 정규화한다."""
    display = df.copy()
    for col in display.columns:
        series = display[col]
        if pd.api.types.is_numeric_dtype(series):
            display[col] = series
            continue
        if pd.api.types.is_datetime64_any_dtype(series):
            display[col] = series.astype("string").fillna("")
            continue
        display[col] = series.map(_blank_if_empty).astype("string")
    return display


def for_preview_display(df: pd.DataFrame) -> pd.DataFrame:
    """미리보기용 DataFrame — Arrow/Streamlit 호환을 위해 컬럼명은 고유하게 유지한다."""
    return for_display(df)


def preview_column_labels(columns: list[str]) -> dict[str, str]:
    """미리보기 헤더 라벨 — 병합 셀처럼 같은 이름을 반복해 표시한다."""
    labels = merged_header_display_labels(columns)
    return {str(column): label for column, label in zip(columns, labels, strict=True)}


def merged_header_display_labels(columns: list[str]) -> list[str]:
    labels: list[str] = []
    for column in columns:
        column_text = str(column)
        match = _MERGED_SUFFIX_RE.fullmatch(column_text)
        if match and labels and labels[-1] == match.group(1):
            labels.append(match.group(1))
            continue
        labels.append(merged_header_base(column_text))
    return labels


def _blank_if_empty(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in {"none", "nan", "nat", "<na>"}:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return text
