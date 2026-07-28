"""예실대비표·예산 표 전용 규칙 기반 요약."""

from __future__ import annotations

import pandas as pd

from core.constants import (
    BUDGET_BUDGET_COLUMN_CANDIDATES,
    BUDGET_CATEGORY_COLUMN_CANDIDATES,
    BUDGET_COLUMN_HINTS,
    BUDGET_CURRENT_REMAINING_COLUMN_CANDIDATES,
    BUDGET_DETECT_MIN_HITS,
    BUDGET_EXECUTED_COLUMN_CANDIDATES,
    BUDGET_FOOTER_LABELS,
    BUDGET_INTRO,
    BUDGET_ITEM_COLUMN_CANDIDATES,
    BUDGET_KEY_COLUMN_HINTS,
    BUDGET_REMAINING_COLUMN_CANDIDATES,
    SUMMARY_TOP_N,
)
from core.summary_utils import (
    cell_text,
    compact,
    fmt_won,
    is_excluded_summary_label,
    is_grand_total_label,
    is_numeric_col,
)


def looks_like_budget_table(df: pd.DataFrame) -> bool:
    joined = " ".join(str(c) for c in df.columns)
    hits = sum(1 for hint in BUDGET_COLUMN_HINTS if hint in joined)
    return hits >= BUDGET_DETECT_MIN_HITS


def build_budget_summary(
    df: pd.DataFrame,
    *,
    file_name: str | None,
    sheet_name: str | None,
    sheets: list[str],
    excel_shape: tuple[int, int] | None,
) -> str:
    _ = file_name
    rows, cols = excel_shape or (len(df), len(df.columns))
    sheet_label = sheet_name or (sheets[0] if sheets else "Sheet1")
    sheet_count = len(sheets) if sheets else 1

    item_col = _pick_column(df, BUDGET_ITEM_COLUMN_CANDIDATES)
    category_col = _pick_column(df, BUDGET_CATEGORY_COLUMN_CANDIDATES)
    budget_col = _pick_column(df, BUDGET_BUDGET_COLUMN_CANDIDATES)
    executed_col = _pick_column(df, BUDGET_EXECUTED_COLUMN_CANDIDATES)
    remaining_col = _pick_column(df, BUDGET_REMAINING_COLUMN_CANDIDATES)
    current_remaining_col = _pick_column(
        df,
        BUDGET_CURRENT_REMAINING_COLUMN_CANDIDATES,
    )

    total_row = _find_grand_total_row(df, category_col)
    detail = _detail_rows(df, category_col)

    budget = _row_or_sum(total_row, detail, budget_col)
    executed = _row_or_sum(total_row, detail, executed_col)
    remaining = _row_or_sum(total_row, detail, remaining_col)
    rate = (executed / budget * 100.0) if budget and budget > 0 and executed is not None else None

    categories = _major_categories(df, category_col)
    top_executed = _top_items(detail, item_col, executed_col, n=SUMMARY_TOP_N)
    top_remaining = _top_items(detail, item_col, remaining_col, n=SUMMARY_TOP_N)
    negatives = _negative_items(detail, item_col, current_remaining_col or remaining_col)
    key_columns = _key_budget_columns(df.columns)

    lines: list[str] = []
    lines.append(BUDGET_INTRO)
    lines.append("")
    lines.append(f"* 시트 수: {sheet_count}개 (`{sheet_label}`)")
    lines.append(f"* 실제 데이터 범위: {rows}행 × {cols}열")
    if categories:
        lines.append(f"* 주요 항목: {', '.join(categories)}")
    if budget is not None:
        lines.append(f"* 전체 예산: **{fmt_won(budget)}**")
    if executed is not None:
        lines.append(f"* 누적 집행액: **{fmt_won(executed)}**")
    if remaining is not None:
        lines.append(f"* 전체 예산잔액: **{fmt_won(remaining)}**")
    if rate is not None:
        lines.append(f"* 전체 집행률: 약 **{rate:.1f}%**")

    if top_executed:
        lines.append("")
        lines.append(_format_ranking_sentence("집행액은", top_executed, "으로 가장 크고", "순입니다."))

    if top_remaining:
        lines.append("")
        lines.append(_format_ranking_sentence("잔액이 큰 항목은", top_remaining, "", "입니다."))

    if negatives:
        lines.append("")
        names = "와 ".join(name for name, _ in negatives) if len(negatives) == 2 else ", ".join(
            name for name, _ in negatives
        )
        amounts = ", ".join(f"**{fmt_won(value)}**" for _, value in negatives)
        if len(negatives) == 2:
            amounts = f"각각 {amounts}"
        balance_label = "당해 잔액" if current_remaining_col else "예산잔액"
        lines.append(
            f"또한 {names}의 {balance_label}이 {amounts}으로 음수이므로 "
            "예산 초과 또는 이월·가집행 처리 여부를 확인할 필요가 있습니다."
        )

    if key_columns:
        lines.append("")
        col_list = ", ".join(f"`{name}`" for name in key_columns)
        footer_note = ""
        if _has_footer_labels(df, category_col):
            footer_names = ", ".join(BUDGET_FOOTER_LABELS)
            footer_note = f" 하단에는 {footer_names}, 전체 합계가 정리되어 있습니다."
        lines.append(
            f"표에는 {col_list} 등의 컬럼이 있으며,{footer_note}"
            if footer_note
            else f"표에는 {col_list} 등의 컬럼이 있습니다."
        )

    return "\n".join(lines).rstrip()


def _pick_column(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    columns = [str(c) for c in df.columns]
    for wanted in candidates:
        for column in columns:
            if column == wanted:
                return column
    for wanted in candidates:
        for column in columns:
            if wanted in column:
                return column
    return None


def _find_grand_total_row(
    df: pd.DataFrame,
    category_col: str | None,
) -> pd.Series | None:
    """하단 최종 합계 행을 찾는다. 소계·내부흡수·외부유출은 제외."""
    for index in reversed(range(len(df))):
        row = df.iloc[index]
        labels = [cell_text(row.get(col)) for col in df.columns if not is_numeric_col(df, col)]
        if category_col:
            labels.insert(0, cell_text(row.get(category_col)))
        if any(is_grand_total_label(label) for label in labels if label):
            return row
    return None


def _detail_rows(df: pd.DataFrame, category_col: str | None) -> pd.DataFrame:
    """소계·합계·내부흡수액·외부유출액 행을 제외한 세부 항목."""
    mask = pd.Series(True, index=df.index)
    for column in df.columns:
        if is_numeric_col(df, column):
            continue
        mask &= ~df[column].map(is_excluded_summary_label)
    if category_col and category_col in df.columns:
        mask &= ~df[category_col].map(is_excluded_summary_label)
    return df.loc[mask].copy()


def _major_categories(df: pd.DataFrame, category_col: str | None) -> list[str]:
    if not category_col or category_col not in df.columns:
        return []
    values: list[str] = []
    seen: set[str] = set()
    for value in df[category_col].tolist():
        text = cell_text(value)
        if not text or is_excluded_summary_label(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        values.append(text)
    return values


def _top_items(
    detail: pd.DataFrame,
    item_col: str | None,
    metric_col: str | None,
    *,
    n: int,
) -> list[tuple[str, float]]:
    if detail.empty or not item_col or not metric_col:
        return []
    if item_col not in detail.columns or metric_col not in detail.columns:
        return []

    work = detail[[item_col, metric_col]].copy()
    work[metric_col] = pd.to_numeric(work[metric_col], errors="coerce")
    work[item_col] = work[item_col].map(cell_text)
    work = work.dropna(subset=[metric_col])
    work = work[work[item_col].astype(bool)]
    work = work[~work[item_col].map(is_excluded_summary_label)]
    if work.empty:
        return []

    grouped = (
        work.groupby(item_col, as_index=False)[metric_col]
        .sum()
        .sort_values(metric_col, ascending=False)
        .head(n)
    )
    return [
        (str(row[item_col]), float(row[metric_col]))
        for _, row in grouped.iterrows()
    ]


def _negative_items(
    detail: pd.DataFrame,
    item_col: str | None,
    metric_col: str | None,
) -> list[tuple[str, float]]:
    if detail.empty or not item_col or not metric_col:
        return []
    if item_col not in detail.columns or metric_col not in detail.columns:
        return []

    items: list[tuple[str, float]] = []
    for _, row in detail.iterrows():
        name = cell_text(row.get(item_col))
        if not name or is_excluded_summary_label(name):
            continue
        value = pd.to_numeric(row.get(metric_col), errors="coerce")
        if pd.isna(value) or float(value) >= 0:
            continue
        items.append((name, float(value)))
    return items


def _row_or_sum(
    total_row: pd.Series | None,
    detail: pd.DataFrame,
    column: str | None,
) -> float | None:
    if not column:
        return None
    if total_row is not None and column in total_row.index:
        value = pd.to_numeric(total_row.get(column), errors="coerce")
        if pd.notna(value):
            return float(value)
    if column not in detail.columns:
        return None
    total = pd.to_numeric(detail[column], errors="coerce").sum(skipna=True)
    if pd.isna(total):
        return None
    return float(total)


def _key_budget_columns(columns: pd.Index) -> list[str]:
    found: list[str] = []
    for hint in BUDGET_KEY_COLUMN_HINTS:
        for column in columns:
            text = str(column)
            base = text.split("_", 1)[0]
            if hint in (text, base) or text.startswith(f"{hint}_"):
                if hint not in found:
                    found.append(hint)
                break
    return found


def _has_footer_labels(df: pd.DataFrame, category_col: str | None) -> bool:
    if not category_col or category_col not in df.columns:
        return False
    labels = {compact(cell_text(v)) for v in df[category_col].tolist()}
    return any(compact(label) in labels for label in BUDGET_FOOTER_LABELS)


def _format_ranking_sentence(
    prefix: str,
    items: list[tuple[str, float]],
    first_suffix: str,
    end_suffix: str,
) -> str:
    if not items:
        return ""
    first_name, first_value = items[0]
    if len(items) == 1:
        return f"{prefix} {first_name}가 {fmt_won(first_value)}{first_suffix or end_suffix}."
    rest = ", ".join(f"{name} {fmt_won(value)}" for name, value in items[1:])
    if first_suffix:
        return f"{prefix} {first_name}가 {fmt_won(first_value)}{first_suffix}, {rest} {end_suffix}"
    return f"{prefix} {first_name} {fmt_won(first_value)}, {rest}{end_suffix}"
