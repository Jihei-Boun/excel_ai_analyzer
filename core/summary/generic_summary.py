"""일반 표 형태 데이터용 규칙 기반 요약."""

from __future__ import annotations

import pandas as pd

from core.constants import (
    SUMMARY_NUMERIC_COLS,
    SUMMARY_PREVIEW_COLS,
    SUMMARY_TOP_N,
)
from core.summary.summary_utils import cell_text, fmt_number

_COPY = {
    "ko": {
        "empty": "데이터가 비어 있어 요약할 내용이 없습니다.",
        "intro_named": "이 파일(`{file_name}`)은 {rows}행 × {cols}열 표 형태 데이터입니다.",
        "intro": "이 파일은 {rows}행 × {cols}열 표 형태 데이터입니다.",
        "sheet_count": "* 시트 수: {count}개{label}",
        "sheet_label": " (`{name}`)",
        "data_range": "* 실제 데이터 범위: {rows}행 × {cols}열",
        "numeric_cols": "* 수치형 컬럼: {count}개",
        "text_cols": "* 문자형 컬럼: {count}개",
        "main_cols": "* 주요 컬럼: {cols}{more}",
        "more_cols": " 외 {count}개",
        "top_values_header": "문자형 컬럼 상위 값 (`{column}`):",
        "top_value": "* {name} ({count}건)",
        "numeric_header": "주요 수치 컬럼 (합 / 최소 / 최대):",
        "numeric_row": "* `{col}`: 합 {total} / 최소 {minimum} / 최대 {maximum}",
        "multi_empty": "요약할 {unit}이(가) 없습니다.",
        "multi_intro": "선택된 {unit} {count}개를 요약합니다.\n",
    },
    "en": {
        "empty": "The data is empty, so there is nothing to summarize.",
        "intro_named": (
            "This file (`{file_name}`) is a table with {rows} rows × {cols} columns."
        ),
        "intro": "This file is a table with {rows} rows × {cols} columns.",
        "sheet_count": "* Sheets: {count}{label}",
        "sheet_label": " (`{name}`)",
        "data_range": "* Data range: {rows} rows × {cols} columns",
        "numeric_cols": "* Numeric columns: {count}",
        "text_cols": "* Text columns: {count}",
        "main_cols": "* Key columns: {cols}{more}",
        "more_cols": " +{count} more",
        "top_values_header": "Top values in text column (`{column}`):",
        "top_value": "* {name} ({count})",
        "numeric_header": "Key numeric columns (sum / min / max):",
        "numeric_row": (
            "* `{col}`: sum {total} / min {minimum} / max {maximum}"
        ),
        "multi_empty": "No {unit} selected to summarize.",
        "multi_intro": "Summarizing {count} selected {unit}(s).\n",
    },
}


def summary_copy(*, locale: str | None = None) -> dict[str, str]:
    from core.common.locale_support import normalize_locale

    key = normalize_locale(locale)
    return dict(_COPY.get(key) or _COPY["ko"])


def build_generic_summary(
    df: pd.DataFrame,
    *,
    file_name: str | None,
    sheet_name: str | None,
    sheets: list[str],
    excel_shape: tuple[int, int] | None,
    profile_name: str | None = None,
    locale: str | None = None,
) -> str:
    from core.profile_loader import locale_for

    resolved_locale = locale or locale_for(profile_name=profile_name)
    copy = summary_copy(locale=resolved_locale)

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

    if file_name:
        intro = copy["intro_named"].format(
            file_name=file_name, rows=rows, cols=cols
        )
    else:
        intro = copy["intro"].format(rows=rows, cols=cols)

    label = (
        copy["sheet_label"].format(name=sheet_label) if sheet_label else ""
    )
    lines = [
        intro,
        "",
        copy["sheet_count"].format(count=sheet_count, label=label),
        copy["data_range"].format(rows=rows, cols=cols),
        copy["numeric_cols"].format(count=len(numeric_cols)),
        copy["text_cols"].format(count=len(text_cols)),
    ]
    preview_cols = [str(c) for c in list(df.columns)[:SUMMARY_PREVIEW_COLS]]
    more = (
        ""
        if len(df.columns) <= SUMMARY_PREVIEW_COLS
        else copy["more_cols"].format(
            count=len(df.columns) - SUMMARY_PREVIEW_COLS
        )
    )
    lines.append(
        copy["main_cols"].format(
            cols=", ".join(f"`{c}`" for c in preview_cols),
            more=more,
        )
    )

    if text_cols:
        top_values = _top_categorical_values(df, text_cols[0], n=SUMMARY_TOP_N)
        if top_values:
            lines.append("")
            lines.append(copy["top_values_header"].format(column=text_cols[0]))
            for name, count in top_values:
                lines.append(copy["top_value"].format(name=name, count=count))

    if numeric_cols:
        lines.append("")
        lines.append(copy["numeric_header"])
        for col in numeric_cols[:SUMMARY_NUMERIC_COLS]:
            series = pd.to_numeric(df[col], errors="coerce")
            total = float(series.sum(skipna=True))
            minimum = float(series.min(skipna=True))
            maximum = float(series.max(skipna=True))
            lines.append(
                copy["numeric_row"].format(
                    col=col,
                    total=fmt_number(total),
                    minimum=fmt_number(minimum),
                    maximum=fmt_number(maximum),
                )
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
