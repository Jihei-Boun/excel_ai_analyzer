"""일반 표 형태 데이터용 규칙 기반 요약."""

from __future__ import annotations

import pandas as pd

from core.constants import (
    SUMMARY_NUMERIC_COLS,
    SUMMARY_PREVIEW_COLS,
    SUMMARY_TOP_N,
)
from core.summary.summary_utils import cell_text, fmt_number


def build_generic_summary(
    df: pd.DataFrame,
    *,
    file_name: str | None,
    sheet_name: str | None,
    sheets: list[str],
    excel_shape: tuple[int, int] | None,
) -> str:
    rows, cols = excel_shape or (len(df), len(df.columns))
    sheet_label = sheet_name or (sheets[0] if sheets else None)
    sheet_count = len(sheets) if sheets else 1
    numeric_cols = [
        col
        for col in df.columns
        if not pd.api.types.is_datetime64_any_dtype(df[col])
        and pd.to_numeric(df[col], errors="coerce").notna().any()
    ]
    text_cols = [col for col in df.columns if col not in numeric_cols]

    lines = [
        (
            f"이 파일(`{file_name}`)은 {rows}행 × {cols}열 표 형태 데이터입니다."
            if file_name
            else f"이 파일은 {rows}행 × {cols}열 표 형태 데이터입니다."
        ),
        "",
        f"* 시트 수: {sheet_count}개"
        + (f" (`{sheet_label}`)" if sheet_label else ""),
        f"* 실제 데이터 범위: {rows}행 × {cols}열",
        f"* 수치형 컬럼: {len(numeric_cols)}개",
        f"* 문자형 컬럼: {len(text_cols)}개",
    ]
    preview_cols = [str(c) for c in list(df.columns)[:SUMMARY_PREVIEW_COLS]]
    more = (
        ""
        if len(df.columns) <= SUMMARY_PREVIEW_COLS
        else f" 외 {len(df.columns) - SUMMARY_PREVIEW_COLS}개"
    )
    lines.append(f"* 주요 컬럼: {', '.join(f'`{c}`' for c in preview_cols)}{more}")

    if text_cols:
        top_values = _top_categorical_values(df, text_cols[0], n=SUMMARY_TOP_N)
        if top_values:
            lines.append("")
            lines.append(f"문자형 컬럼 상위 값 (`{text_cols[0]}`):")
            for name, count in top_values:
                lines.append(f"* {name} ({count}건)")

    if numeric_cols:
        lines.append("")
        lines.append("주요 수치 컬럼 (합 / 최소 / 최대):")
        for col in numeric_cols[:SUMMARY_NUMERIC_COLS]:
            series = pd.to_numeric(df[col], errors="coerce")
            total = float(series.sum(skipna=True))
            minimum = float(series.min(skipna=True))
            maximum = float(series.max(skipna=True))
            lines.append(
                f"* `{col}`: 합 {fmt_number(total)} / "
                f"최소 {fmt_number(minimum)} / 최대 {fmt_number(maximum)}"
            )

    return "\n".join(lines)


def _top_categorical_values(
    df: pd.DataFrame,
    column: object,
    *,
    n: int,
) -> list[tuple[str, int]]:
    if column not in df.columns:
        return []
    counts: dict[str, int] = {}
    for value in df[column].tolist():
        text = cell_text(value)
        if not text:
            continue
        counts[text] = counts.get(text, 0) + 1
    if not counts:
        return []
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [(name, count) for name, count in ranked[:n]]
