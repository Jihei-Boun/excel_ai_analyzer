"""표시용 DataFrame 헬퍼."""

from __future__ import annotations

import html
import re

import pandas as pd
import streamlit as st

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
    """현재 테마 CSS 변수로 표를 그린다 (색상은 HTML에 넣지 않음).

    st.dataframe(Glide 캔버스)은 Streamlit 네이티브 테마만 따르므로
    앱 테마 토글과 어긋난다. 매 rerun마다 데이터만으로 HTML을 재생성한다.
    """
    display = for_display(df)
    labels = dict(column_labels or {})
    if column_config:
        for key, config in column_config.items():
            label = getattr(config, "label", None)
            if label:
                labels[str(key)] = str(label)

    table_html = _app_html_table(
        display,
        height=height,
        labels=labels,
        hide_index=hide_index,
    )
    if hasattr(st, "html"):
        st.html(table_html)
    else:
        st.markdown(table_html, unsafe_allow_html=True)


def _app_html_table(
    df: pd.DataFrame,
    *,
    height: int,
    labels: dict[str, str],
    hide_index: bool,
) -> str:
    """색상 없는 HTML 표 — 스타일은 .app-df CSS 변수가 담당."""
    columns = [str(col) for col in df.columns]
    header_cells: list[str] = []
    if not hide_index:
        header_cells.append("<th></th>")
    for col in columns:
        title = html.escape(labels.get(col, col))
        header_cells.append(f"<th>{title}</th>")

    body_rows: list[str] = []
    for idx, row in df.iterrows():
        cells: list[str] = []
        if not hide_index:
            cells.append(f"<td>{html.escape(_format_cell_value(idx))}</td>")
        for col in columns:
            cells.append(f"<td>{html.escape(_format_cell_value(row[col]))}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    max_h = max(120, int(height))
    return (
        f'<div class="app-df-wrap" style="max-height:{max_h}px;overflow:auto;">'
        f'<table class="app-df">'
        f"<thead><tr>{''.join(header_cells)}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        f"</table></div>"
    )


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
