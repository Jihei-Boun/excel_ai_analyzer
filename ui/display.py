"""표시용 DataFrame 헬퍼 — Streamlit 네이티브 컴포넌트 사용."""

from __future__ import annotations

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
    """DataFrame을 Streamlit 네이티브 st.dataframe으로 표시한다."""
    display = for_display(df)
    config = dict(column_config or {})
    if column_labels:
        for key, label in column_labels.items():
            if key not in config:
                config[key] = st.column_config.Column(label=label)

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=hide_index,
        height=max(120, int(height)),
        column_config=config or None,
    )


def render_analysis_result(result: object) -> None:
    """AI/연산 결과 타입별 네이티브 출력."""
    if result is None:
        st.warning("표시할 분석 결과가 없습니다.")
        return

    if isinstance(result, pd.DataFrame):
        render_dataframe(result, hide_index=True)
        return

    if isinstance(result, pd.Series):
        render_dataframe(result.reset_index(), hide_index=True)
        return

    if isinstance(result, dict):
        try:
            render_dataframe(pd.DataFrame(result), hide_index=True)
        except Exception:
            st.json(result)
        return

    if isinstance(result, list):
        try:
            render_dataframe(pd.DataFrame(result), hide_index=True)
        except Exception:
            st.write(result)
        return

    # Plotly / Matplotlib 등은 호출 측에서 처리. 여기선 일반 값.
    try:
        display = f"{float(result):,.0f}"
        st.metric("결과", display)
    except (TypeError, ValueError):
        st.write(result)


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
