"""표시용 DataFrame 헬퍼."""

from __future__ import annotations

import html
import re

import pandas as pd
import streamlit as st

from core.excel_loader import merged_header_base

_MERGED_SUFFIX_RE = re.compile(r"^(.+)_(\d+)$")


def for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Streamlit/Arrow가 안전하게 그릴 수 있도록 타입을 정규화한다."""
    display = df.copy()
    for col in display.columns:
        series = display[col]
        if pd.api.types.is_numeric_dtype(series):
            display[col] = _normalize_numeric_series(series)
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
    """미리보기 헤더 라벨.

    - 숫자 접미사 중복(`실행예산_2`)은 상위명만 반복 표시
    - 의미 있는 복합명(`실행예산_이월예산`)은 그대로 표시
    """
    labels: list[str] = []
    for column in columns:
        column_text = str(column)
        match = _MERGED_SUFFIX_RE.fullmatch(column_text)
        if match and labels and labels[-1] == match.group(1):
            labels.append(match.group(1))
            continue
        labels.append(column_text)
    return labels


def render_dataframe(
    df: pd.DataFrame,
    *,
    height: int = 360,
    hide_index: bool = True,
    column_config: dict | None = None,
    column_labels: dict[str, str] | None = None,
) -> None:
    """테마에 맞춰 표를 그린다.

    라이트 모드는 Streamlit 네이티브 dataframe(캔버스)이 다크 테마로 남는 경우가
    있어, 우리가 직접 스타일한 HTML 표로 표시한다.
    """
    display = for_display(df)
    labels = dict(column_labels or {})
    if column_config:
        for key, config in column_config.items():
            label = getattr(config, "label", None)
            if label:
                labels[str(key)] = str(label)

    if st.session_state.get("theme") == "light":
        st.markdown(
            _light_html_table(display, height=height, labels=labels, hide_index=hide_index),
            unsafe_allow_html=True,
        )
        return

    kwargs: dict = {
        "width": "stretch",
        "height": height,
        "hide_index": hide_index,
    }
    if column_config:
        kwargs["column_config"] = column_config
    st.dataframe(display, **kwargs)


def _light_html_table(
    df: pd.DataFrame,
    *,
    height: int,
    labels: dict[str, str],
    hide_index: bool,
) -> str:
    header_cells: list[str] = []
    if not hide_index:
        header_cells.append("<th></th>")
    for column in df.columns:
        title = labels.get(str(column), str(column))
        header_cells.append(f"<th>{html.escape(title)}</th>")

    body_rows: list[str] = []
    for index, row in df.iterrows():
        cells: list[str] = []
        if not hide_index:
            cells.append(f"<td>{html.escape(str(index))}</td>")
        for column in df.columns:
            value = row[column]
            cells.append(f"<td>{html.escape(_format_cell_value(value))}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    return f"""
<div class="light-df-wrap" style="max-height:{height}px;overflow:auto;border:1px solid #d0d7e2;border-radius:8px;background:#ffffff;">
  <table class="light-df">
    <thead><tr>{''.join(header_cells)}</tr></thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table>
</div>
"""


def _normalize_numeric_series(series: pd.Series) -> pd.Series:
    """정수로 떨어지면 int로 바꿔 121.0 같은 표시를 막는다."""
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.isna().all():
        return series
    non_null = numeric.dropna()
    if non_null.empty:
        return series
    if (non_null % 1 == 0).all():
        return numeric.astype("Int64")
    return numeric


def _format_cell_value(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value)
    try:
        import numpy as np

        if isinstance(value, (np.integer,)):
            return str(int(value))
        if isinstance(value, (np.floating,)):
            number = float(value)
            if number.is_integer():
                return str(int(number))
            return str(number)
    except ImportError:
        pass
    text = str(value).strip()
    if text.lower() in {"none", "nan", "nat", "<na>", "<NA>"}:
        return ""
    return text


def _blank_if_empty(value: object) -> str:
    return _format_cell_value(value)
