"""조건형 행 필터 (A가 0인데 B가 있는 등)."""
from __future__ import annotations

import re

import pandas as pd

from core.column_match import resolve_metric_column
from core.constants import BUDGET_FOOTER_LABELS
from core.pandasai_config import exclude_total_rows, prepare_dataframe_for_ai
from core.prompt_intent import is_condition_filter_request
from core.summary_utils import cell_text, compact, is_excluded_summary_label
from core.text_normalize import normalize_text

_ZERO_COL_RE = re.compile(
    r"([0-9A-Za-z가-힣_]+)\s*(?:이|가|은|는)\s*0(?:\D|$)",
)
_EXISTS_COL_RE = re.compile(
    r"([0-9A-Za-z가-힣_]+)\s*(?:이|가|은|는)\s*있는",
)


def try_condition_row_filter(
    df: pd.DataFrame,
    prompt: str,
) -> pd.DataFrame | None:
    """조건형 행 필터. 현재는 'A가 0인데 B가 있는' (==0 & >0)만 규칙 처리한다."""
    if df is None or df.empty or not is_condition_filter_request(prompt):
        return None

    zero_col, exists_col = _parse_zero_and_exists_columns(df, prompt)
    if not zero_col or not exists_col or zero_col == exists_col:
        return None

    work = exclude_total_rows(prepare_dataframe_for_ai(df))
    work = _drop_budget_footer_and_empty_items(work)

    zero_vals = pd.to_numeric(work[zero_col], errors="coerce")
    exists_vals = pd.to_numeric(work[exists_col], errors="coerce")
    mask = (zero_vals.fillna(1) == 0) & (exists_vals.fillna(0) > 0)
    result = work.loc[mask]
    return result.reset_index(drop=True)


def _parse_zero_and_exists_columns(
    df: pd.DataFrame,
    prompt: str,
) -> tuple[str | None, str | None]:
    zero_hint = None
    exists_hint = None
    zero_m = _ZERO_COL_RE.search(prompt)
    if zero_m:
        zero_hint = zero_m.group(1)
    exists_m = _EXISTS_COL_RE.search(prompt)
    if exists_m:
        exists_hint = exists_m.group(1)

    zero_col = _resolve_condition_metric(df, zero_hint) if zero_hint else None
    exists_col = _resolve_condition_metric(df, exists_hint) if exists_hint else None

    if zero_col is None or exists_col is None:
        from core.column_match import find_mentioned_numeric_columns

        mentioned = find_mentioned_numeric_columns(df, prompt)
        if len(mentioned) >= 2:
            if zero_col is None and zero_hint:
                zero_col = next(
                    (
                        c
                        for c in mentioned
                        if normalize_text(zero_hint) in normalize_text(str(c))
                    ),
                    mentioned[0],
                )
            if exists_col is None and exists_hint:
                exists_col = next(
                    (
                        c
                        for c in mentioned
                        if normalize_text(exists_hint) in normalize_text(str(c))
                        and c != zero_col
                    ),
                    next((c for c in mentioned if c != zero_col), None),
                )
    return zero_col, exists_col


def _resolve_condition_metric(df: pd.DataFrame, hint: str | None) -> str | None:
    """조건 필터용 수치 컬럼. ``*_합계`` 열을 우선한다."""
    if not hint:
        return None
    target = normalize_text(hint)
    if not target:
        return None
    scored: list[tuple[int, int, str]] = []
    for column in df.columns:
        norm = normalize_text(str(column))
        if not norm:
            continue
        if target not in norm and norm not in target:
            continue
        coerced = pd.to_numeric(df[column], errors="coerce")
        if not coerced.notna().any() and not pd.api.types.is_numeric_dtype(df[column]):
            continue
        total_rank = 1 if (norm.endswith("합계") or norm.endswith("_합계")) else 0
        scored.append((total_rank, len(norm), str(column)))
    if scored:
        scored.sort(reverse=True)
        return scored[0][2]
    return resolve_metric_column(df, hint)


def _drop_budget_footer_and_empty_items(df: pd.DataFrame) -> pd.DataFrame:
    """소계·footer·항목코드 없는 요약 행을 제외한다."""
    if df is None or df.empty:
        return df
    footer = {compact(label) for label in BUDGET_FOOTER_LABELS}
    keep = []
    for idx, row in df.iterrows():
        label_cols = [c for c in ("비목분류", "비용명_2", "비용명", "항목") if c in df.columns]
        texts = [cell_text(row[c]) for c in label_cols]
        if any(is_excluded_summary_label(t) for t in texts if t):
            keep.append(False)
            continue
        if any(compact(t) in footer for t in texts if t):
            keep.append(False)
            continue
        # 비용명 코드가 있으면 세부 항목으로 본다
        if "비용명" in df.columns:
            code = row["비용명"]
            has_code = pd.notna(code) and str(code).strip() not in ("", "nan")
            if not has_code:
                # 비용명_2만 있는 경우도 허용
                name = cell_text(row["비용명_2"]) if "비용명_2" in df.columns else ""
                if not name:
                    keep.append(False)
                    continue
        keep.append(True)
    return df.loc[keep] if keep else df.iloc[0:0]
