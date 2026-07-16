"""엑셀 파일을 범용 pandas DataFrame으로 읽고 정리한다."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_excel(path: str | Path, *, sheet_name: str | int = 0) -> pd.DataFrame:
    """엑셀 파일을 읽고 기본 전처리를 적용한다."""
    df = pd.read_excel(path, sheet_name=sheet_name)
    return sanitize_dataframe(df)


def sanitize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """원본 의미를 유지하며 컬럼명·빈 행·안전한 숫자 문자열만 정리한다."""
    return _preprocess(df)


def _preprocess(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = _unique_column_names(df.columns)
    df = df.dropna(how="all").reset_index(drop=True)

    for col in df.columns:
        df[col] = _coerce_column(df[col])

    return df


def _unique_column_names(columns: pd.Index) -> list[str]:
    names: list[str] = []
    counts: dict[str, int] = {}
    for index, column in enumerate(columns, start=1):
        base = str(column).strip() or f"column_{index}"
        count = counts.get(base, 0)
        counts[base] = count + 1
        names.append(base if count == 0 else f"{base}_{count + 1}")
    return names


def _coerce_column(series: pd.Series) -> pd.Series:
    """혼합 타입·콤마 숫자를 정리해 Arrow/연산에 맞게 만든다."""
    if pd.api.types.is_bool_dtype(series) or pd.api.types.is_datetime64_any_dtype(series):
        return series

    if pd.api.types.is_numeric_dtype(series):
        return series

    numeric = _to_numeric_series(series)
    if numeric is not None:
        return numeric

    return _to_clean_string_series(series)


def _to_numeric_series(series: pd.Series) -> pd.Series | None:
    cleaned = (
        series.map(_cell_to_text)
        .str.replace(",", "", regex=False)
        .str.strip()
    )
    cleaned = cleaned.replace({"": pd.NA})
    numeric = pd.to_numeric(cleaned, errors="coerce")

    non_empty = cleaned.notna().sum()
    if non_empty == 0:
        return None

    converted = numeric.notna().sum()
    if converted == non_empty:
        return numeric
    return None


def _to_clean_string_series(series: pd.Series) -> pd.Series:
    return series.map(_clean_string_value).astype("string")


def _clean_string_value(value: object) -> object:
    if value is None:
        return pd.NA
    try:
        if pd.isna(value):
            return pd.NA
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _cell_to_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()
