"""표시용 DataFrame 헬퍼."""

from __future__ import annotations

import pandas as pd


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
