"""집계 표·리스트 시드·스칼라 변환."""

from __future__ import annotations

import pandas as pd

from core.column_match import (
    _is_explicit_groupby_prompt,
    find_groupby_column,
    find_mentioned_column,
    find_mentioned_numeric_columns,
    looks_like_code_metric_column,
    resolve_metric_column,
)
from core.constants import BUDGET_FOOTER_LABELS, SUMMARY_RANKING_BITS
from core.pandasai_config import prepare_dataframe_for_ai
from core.prompt_intent import _match_aggregate_op
from core.text_normalize import normalize_text
from core.value_filter import (
    _cell_match_text,
    format_context_label,
)

__all__ = [
    "build_groupby_aggregate_table",
    "build_context_aggregate_table",
    "build_multi_context_aggregate_table",
    "scalar_to_context_table",
    "split_frames_by_source",
    "_build_list_seed_frame",
    "_aggregate_reducer",
    "_is_budget_footer_label",
]

def _build_list_seed_frame(df: pd.DataFrame, prompt: str) -> pd.DataFrame | None:
    """리스트 요청용 최소 DataFrame을 만든다 (후처리에서 그룹/코드 보강)."""
    prepared = prepare_dataframe_for_ai(df)
    mentioned = find_mentioned_column(prepared, prompt)
    if mentioned and mentioned in prepared.columns:
        return prepared[[mentioned]].reset_index(drop=True)

    text_cols = [
        col
        for col in prepared.columns
        if pd.api.types.is_string_dtype(prepared[col]) or prepared[col].dtype == object
    ]
    if not text_cols:
        return None
    return prepared[[text_cols[-1]]].reset_index(drop=True)


def _is_budget_footer_label(value: object) -> bool:
    """프로필 footer_labels에 해당하는 하단 요약 라벨인지."""
    text = _cell_match_text(value)
    if not text:
        return False
    compact = normalize_text(text)
    from core.profile_loader import footer_labels_for

    labels = footer_labels_for()
    if not labels:
        labels = BUDGET_FOOTER_LABELS
    return compact in {normalize_text(label) for label in labels}


def _aggregate_reducer(op: str) -> tuple[str, object]:
    if op == "mean":
        return "평균", lambda s: float(s.mean())
    if op == "max":
        return "최댓값", lambda s: float(s.max())
    if op == "min":
        return "최솟값", lambda s: float(s.min())
    return "총합", lambda s: float(s.sum())


def build_groupby_aggregate_table(
    df: pd.DataFrame,
    prompt: str,
    *,
    use_budget_profile: bool = False,
) -> tuple[pd.DataFrame, str] | None:
    """'비용명별 집행계 합계'처럼 그룹별 집계 표를 만든다.

    '비목분류별 계획예산'처럼 합계 단어가 없어도 X별 Y 요청이면 합산으로 처리한다.
    use_budget_profile=True이면 내부흡수액·외부유출액 등 예산 footer 행을 제외한다.

    NOTE: 질의 해석 단축 경로이다. 장기적으로는 LLM + 범용 실행 유틸로 이관 대상.
    """
    group_col = find_groupby_column(df, prompt)
    if group_col is None or df is None or df.empty or group_col not in df.columns:
        return None

    op = _match_aggregate_op(prompt)
    # "상위 3개 매출 지역" 같은 랭킹 요청은 강제 그룹 합산하지 않고
    # 일반 분석(필터/정렬/LLM) 경로로 넘긴다.
    if op is None and not _is_explicit_groupby_prompt(prompt):
        return None
    op = op or "sum"
    metric_cols = [
        col
        for col in find_mentioned_numeric_columns(df, prompt)
        if col != group_col and not looks_like_code_metric_column(df, col)
    ]
    if not metric_cols:
        return None

    from core.pandasai_config import (
        exclude_total_rows,
        is_total_label,
        prepare_dataframe_for_ai,
        sum_metric_excluding_totals,
    )
    from core.profile_loader import footer_labels_for

    work = exclude_total_rows(prepare_dataframe_for_ai(df))
    if work.empty or group_col not in work.columns:
        return None

    op_name, reduce = _aggregate_reducer(op)
    rows: list[dict[str, object]] = []
    summary_bits: list[str] = []

    label_series = work[group_col].map(_cell_match_text)
    work = work.copy()
    work["_group_label"] = label_series
    work = work[work["_group_label"].astype(bool)]
    if work.empty:
        return None

    drop_footers = use_budget_profile or bool(footer_labels_for())

    # 파일에 처음 등장하는 순서 유지 (가나다·금액 정렬 금지)
    ordered_labels: list[str] = []
    seen_labels: set[str] = set()
    for label in work["_group_label"].tolist():
        text = str(label)
        if not text or text in seen_labels or is_total_label(text):
            continue
        if drop_footers and _is_budget_footer_label(text):
            continue
        seen_labels.add(text)
        ordered_labels.append(text)

    for label in ordered_labels:
        group = work.loc[work["_group_label"] == label]
        row: dict[str, object] = {str(group_col): label}
        for metric_col in metric_cols:
            resolved = resolve_metric_column(group, metric_col) or metric_col
            if resolved not in group.columns:
                continue
            if op == "sum":
                value = sum_metric_excluding_totals(group, resolved)
            else:
                value = reduce(pd.to_numeric(group[resolved], errors="coerce"))
            if value is None or pd.isna(value):
                continue
            row[str(metric_col)] = value
        if len(row) <= 1:
            continue
        rows.append(row)

    if not rows:
        return None

    table = pd.DataFrame(rows)
    ordered = [str(group_col)] + [c for c in metric_cols if c in table.columns]
    table = table[ordered + [c for c in table.columns if c not in ordered]].reset_index(drop=True)

    for _, row in table.iterrows():
        metric_parts = [
            f"{col} {op_name}: {float(row[col]):,.0f}"
            for col in metric_cols
            if col in table.columns and pd.notna(row[col])
        ]
        if metric_parts:
            summary_bits.append(f"{row[str(group_col)]} → " + " / ".join(metric_parts))

    summary = (
        f"{group_col}별 {op_name} — "
        + " | ".join(summary_bits[:SUMMARY_RANKING_BITS])
    )
    if len(summary_bits) > SUMMARY_RANKING_BITS:
        summary += f" 외 {len(summary_bits) - SUMMARY_RANKING_BITS}개"
    return table, summary


def build_context_aggregate_table(
    df: pd.DataFrame,
    prompt: str,
    *,
    context_label: str | None = None,
) -> tuple[pd.DataFrame, str] | None:
    """필터 맥락 + 집계 요청을 요약 표로 만든다.

    예::
            | 계획예산 | 실행예산
        ----|----------|----------
        연구활동비 | 12345    | 67890
    """
    op = _match_aggregate_op(prompt)
    if op is None or df is None or df.empty:
        return None

    metric_cols = find_mentioned_numeric_columns(df, prompt)
    # 코드성 수치(비용명 121 등)는 금액 합계에 쓰지 않는다.
    metric_cols = [
        col
        for col in metric_cols
        if not looks_like_code_metric_column(df, col)
    ]
    if not metric_cols:
        return None

    # '비용명별 …' 요청은 그룹 집계 경로를 우선한다.
    if find_groupby_column(df, prompt) is not None:
        return None

    op_name, reduce = _aggregate_reducer(op)
    row_label = format_context_label(context_label)
    row: dict[str, object] = {"": row_label}
    summary_parts: list[str] = []

    for metric_col in metric_cols:
        col = resolve_metric_column(df, metric_col)
        if col is None:
            continue
        from core.pandasai_config import (
            exclude_total_rows,
            prepare_dataframe_for_ai,
            sum_metric_excluding_totals,
        )

        if op == "sum":
            value = sum_metric_excluding_totals(df, col)
        else:
            work = exclude_total_rows(prepare_dataframe_for_ai(df))
            value = reduce(pd.to_numeric(work[col], errors="coerce"))
        if value is None or pd.isna(value):
            continue
        row[str(metric_col)] = value
        summary_parts.append(f"{metric_col} {op_name}: {value:,.0f}")

    if len(row) <= 1:
        return None

    table = pd.DataFrame([row])
    summary = f"{row_label} · " + " / ".join(summary_parts)
    return table, summary


def build_multi_context_aggregate_table(
    named_dfs: list[tuple[str, pd.DataFrame]],
    prompt: str,
    *,
    context_label: str | None = None,
    unit_label: str = "파일",
) -> tuple[pd.DataFrame, str] | None:
    """다중 파일/시트 집계를 단위별 행 · 수치 컬럼 열 요약 표로 만든다.

    예::
                     | 계획예산
        -------------|----------
        5예실대비표.xlsx | 79977000
        4예실대비표.xlsx | 46410000
    """
    op = _match_aggregate_op(prompt)
    if op is None or not named_dfs:
        return None

    # 컬럼 탐색은 행이 있는 첫 프레임 기준
    probe = next((df for _, df in named_dfs if df is not None and not df.empty), None)
    if probe is None:
        return None

    metric_cols = find_mentioned_numeric_columns(probe, prompt)
    if not metric_cols:
        # 다른 파일에만 컬럼명이 있을 수 있음
        for _, df in named_dfs:
            metric_cols = find_mentioned_numeric_columns(df, prompt)
            if metric_cols:
                break
    if not metric_cols:
        return None

    # 코드성 수치는 금액 합계에서 제외
    metric_cols = [
        col for col in metric_cols if not looks_like_code_metric_column(probe, col)
    ]
    if not metric_cols:
        return None

    op_name, reduce = _aggregate_reducer(op)
    ctx = format_context_label(context_label)
    rows: list[dict[str, object]] = []
    summary_parts: list[str] = []

    for file_name, df in named_dfs:
        if df is None or df.empty:
            continue
        # 단일파일 요약 표와 동일하게 첫 열은 라벨, 나머지는 수치 컬럼
        row_label = f"{ctx} · {file_name}" if ctx and ctx != "합계" else file_name
        row: dict[str, object] = {"출처파일": row_label}
        file_bits: list[str] = []
        for metric_col in metric_cols:
            col = resolve_metric_column(df, metric_col)
            if col is None:
                continue
            from core.pandasai_config import (
                exclude_total_rows,
                prepare_dataframe_for_ai,
                sum_metric_excluding_totals,
            )

            if op == "sum":
                value = sum_metric_excluding_totals(df, col)
            else:
                work = exclude_total_rows(prepare_dataframe_for_ai(df))
                if col not in work.columns:
                    continue
                value = reduce(pd.to_numeric(work[col], errors="coerce"))
            if value is None or pd.isna(value):
                continue
            row[str(metric_col)] = value
            file_bits.append(f"{metric_col} {op_name}: {value:,.0f}")
        if len(row) <= 1:
            continue
        rows.append(row)
        summary_parts.append(f"{file_name} → " + " / ".join(file_bits))

    if not rows:
        return None

    table = pd.DataFrame(rows)
    # 열 순서: 출처파일 → 요청한 수치 컬럼 순
    ordered = ["출처파일"] + [c for c in metric_cols if c in table.columns]
    extra = [c for c in table.columns if c not in ordered]
    table = table[ordered + extra]

    prefix = f"{ctx} · {unit_label}별" if ctx and ctx != "합계" else f"{unit_label}별"
    summary = f"{prefix} {op_name} — " + " | ".join(summary_parts)
    return table, summary


def split_frames_by_source(
    df: pd.DataFrame,
    *,
    source_col: str = "출처파일",
) -> list[tuple[str, pd.DataFrame]]:
    """합쳐진 다중 파일 결과를 (파일명, DataFrame) 목록으로 나눈다."""
    if df is None or df.empty or source_col not in df.columns:
        return []
    parts: list[tuple[str, pd.DataFrame]] = []
    for name, group in df.groupby(source_col, sort=False):
        part = group.drop(columns=[source_col]).reset_index(drop=True)
        parts.append((str(name), part))
    return parts


def scalar_to_context_table(
    value: object,
    prompt: str,
    df: pd.DataFrame,
    *,
    context_label: str | None = None,
) -> pd.DataFrame | None:
    """숫자 스칼라 결과를 맥락 요약 표로 변환한다."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None

    metric_cols = find_mentioned_numeric_columns(df, prompt)
    # 스칼라는 값이 하나뿐이므로, 언급 컬럼이 여러 개면 변환하지 않는다
    # (다중 컬럼 집계는 build_context_aggregate_table이 담당)
    if len(metric_cols) > 1:
        return None
    metric_col = metric_cols[0] if metric_cols else "값"

    row_label = format_context_label(context_label)
    return pd.DataFrame({"": [row_label], str(metric_col): [number]})
