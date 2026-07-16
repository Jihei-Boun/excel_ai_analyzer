"""엑셀 읽기, 전처리."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

_SECTION_END_RE = re.compile(r"^(소\s*계|합\s*계)$")


def load_excel(path: str | Path, *, sheet_name: str | int = 0) -> pd.DataFrame:
    """엑셀 파일을 읽고 기본 전처리를 적용한다."""
    df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
    return sanitize_dataframe(df)


def sanitize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """혼합 타입·콤마 숫자·비목 구간 등을 정리한다. 재적용해도 안전하다."""
    return _preprocess(df)


def _preprocess(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [_normalize_column_name(col) for col in df.columns]
    df = _rename_merged_headers(df)
    if "비목섹션" in df.columns:
        df = df.drop(columns=["비목섹션"])
    df = df.dropna(how="all").reset_index(drop=True)

    for col in df.columns:
        df[col] = _coerce_column(df[col])

    if "비목분류" in df.columns:
        df["비목분류"] = df["비목분류"].map(_normalize_category_label).astype("string")
        df["비목섹션"] = _build_section_labels(df["비목분류"]).astype("string")

    return df


def _normalize_column_name(name: object) -> str:
    text = str(name).strip()
    return text if text else "unnamed"


def _rename_merged_headers(df: pd.DataFrame) -> pd.DataFrame:
    """비용명 옆 Unnamed 컬럼(실제 세부 명칭)을 세부비목으로 바꾼다."""
    cols = list(df.columns)
    renamed = False
    for i, col in enumerate(cols):
        if str(col).startswith("Unnamed") and i > 0 and cols[i - 1] == "비용명":
            cols[i] = "세부비목"
            renamed = True
    if renamed:
        df = df.copy()
        df.columns = cols
    return df


def _normalize_category_label(value: object) -> str:
    text = _cell_to_text(value)
    if not text:
        return ""
    compact = re.sub(r"\s+", "", text)
    if _SECTION_END_RE.match(compact) or compact == "합계":
        if compact.startswith("소"):
            return "소계"
        return "합계"
    return text


def _build_section_labels(category: pd.Series) -> pd.Series:
    """비목분류 헤더~소계 전 행에 동일한 섹션명을 부여한다.

    예실대비표는 비목분류에 '연구활동비'가 첫 행에만 있고,
    이후 세부 행은 비어 있으며 '소계'에서 구간이 끝난다.
    """
    labels: list[str] = []
    current = ""
    for raw in category.tolist():
        label = _cell_to_text(raw)
        if not label:
            labels.append(current)
            continue
        if label in {"소계", "합계"}:
            labels.append("")
            current = ""
            continue
        current = label
        labels.append(current)
    return pd.Series(labels, index=category.index)


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
        .str.replace("원", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.strip()
    )
    cleaned = cleaned.replace({"": pd.NA, "-": pd.NA, "nan": pd.NA, "none": pd.NA, "nat": pd.NA})
    numeric = pd.to_numeric(cleaned, errors="coerce")

    non_empty = cleaned.notna().sum()
    if non_empty == 0:
        return None

    converted = numeric.notna().sum()
    if converted / non_empty >= 0.7:
        return numeric
    return None


def _to_clean_string_series(series: pd.Series) -> pd.Series:
    return series.map(_cell_to_text).astype("string")


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
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat", "<na>"}:
        return ""
    return text
