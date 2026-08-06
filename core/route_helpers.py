"""프롬프트 라우팅 공유 헬퍼 (Streamlit 없음)."""

from __future__ import annotations

import pandas as pd

from core.aggregates import (
    build_context_aggregate_table,
    build_groupby_aggregate_table,
    split_frames_by_source,
)
from core.prompt_intent import (
    _expects_plot,
    detect_aggregate_op,
)
from core.result_format import exclude_aggregate_rows, restore_source_row_order, to_list_display
from core.text_normalize import _normalize_text
from core.value_filter import (
    _filter_by_mentioned_value,
    _filter_multi_by_mentioned_value,
    build_filter_summary,
    extract_matched_value,
    infer_context_label,
    is_metric_aggregate_request,
)


def needs_chart_context(prompt: str, df: pd.DataFrame) -> bool:
    """무엇을 그릴지 명시 없이 차트만 요청했는지."""
    from core.column_match import (
        find_groupby_column,
        find_mentioned_numeric_columns,
    )

    if not _expects_plot(prompt):
        return False
    if find_groupby_column(df, prompt) is not None:
        return False
    if find_mentioned_numeric_columns(df, prompt):
        return False
    if is_metric_aggregate_request(prompt, df):
        return False
    return True


def resolve_chart_table(
    source: pd.DataFrame,
    prompt: str,
    *,
    context_label: str | None,
    profile_name: str | None = None,
    use_budget_profile: bool = False,
    prior_aggregate_df: pd.DataFrame | None = None,
    prior_aggregate_prompt: str | None = None,
    last_assistant_df: pd.DataFrame | None = None,
    prior_user_prompt: str | None = None,
) -> tuple[pd.DataFrame | None, str]:
    """차트에 쓸 DataFrame과 컬럼 해석용 프롬프트를 결정한다."""
    chart_prompt = prompt

    if not needs_chart_context(prompt, source):
        grouped = build_groupby_aggregate_table(
            source,
            prompt,
            profile_name=profile_name, use_budget_profile=use_budget_profile,
        )
        if grouped is not None:
            return grouped[0], prompt
        contextual = build_context_aggregate_table(
            source,
            prompt,
            context_label=context_label,
        )
        if contextual is not None:
            return contextual[0], prompt
        return None, prompt

    if isinstance(prior_aggregate_df, pd.DataFrame) and not prior_aggregate_df.empty:
        stored_prompt = str(prior_aggregate_prompt or "")
        return prior_aggregate_df, stored_prompt or prompt

    if last_assistant_df is not None and not last_assistant_df.empty:
        return last_assistant_df, prior_user_prompt or prompt

    if prior_user_prompt:
        grouped = build_groupby_aggregate_table(
            source,
            prior_user_prompt,
            profile_name=profile_name, use_budget_profile=use_budget_profile,
        )
        if grouped is not None:
            return grouped[0], prior_user_prompt
        contextual = build_context_aggregate_table(
            source,
            prior_user_prompt,
            context_label=context_label,
        )
        if contextual is not None:
            return contextual[0], prior_user_prompt

    return None, prompt


def postprocess_table_result(
    result: pd.DataFrame,
    prompt: str,
    summary: str,
    *,
    source_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, str, dict]:
    """집계 행 제거·원본 순서 복원·리스트 표시 메타를 적용한다."""
    meta: dict = {}
    if detect_aggregate_op(prompt) is None:
        result, excluded = exclude_aggregate_rows(result, prompt)
        if excluded:
            summary = f"{summary} · 합계·소계 {excluded}행 제외"

    result = restore_source_row_order(result, source_df, prompt=prompt)

    list_info = to_list_display(result, prompt, source_df=source_df)
    if list_info is not None:
        meta["list_values"] = list_info.values
        meta["list_label"] = list_info.label
        if list_info.groups:
            meta["list_groups"] = list_info.groups

    return result, summary, meta


def _context_updates_from_filter(
    full_df: pd.DataFrame,
    prompt: str,
    result: pd.DataFrame,
) -> tuple[str | None, str | None]:
    """필터 결과에서 다음 집계용 맥락 라벨·요약을 계산한다."""
    if detect_aggregate_op(prompt) is not None:
        return None, None
    if result is None or result.empty:
        return None, None

    label = infer_context_label(prompt=prompt, result_df=result, full_df=full_df)
    summary = build_filter_summary(prompt, result, full_df)
    if summary:
        return label, summary
    if label:
        return label, label
    return None, None


def _merge_analysis_meta(meta: dict, analysis_meta: dict | None) -> dict:
    if not analysis_meta:
        return meta
    for key in ("code", "chart_path"):
        value = analysis_meta.get(key)
        if value:
            meta[key] = value
    return meta


def _attach_filter_summary_meta(
    meta: dict,
    *,
    prompt: str,
    result: pd.DataFrame,
    full_df: pd.DataFrame | None,
) -> tuple[dict, str | None]:
    summary = build_filter_summary(prompt, result, full_df)
    if summary:
        meta = {**meta, "filter_summary": summary}
        return meta, summary
    return meta, None


def resolve_multi_aggregate_source(
    prepared: list[tuple[str, pd.DataFrame]],
    prompt: str,
    *,
    context_label: str | None,
    filter_df: pd.DataFrame | None,
) -> tuple[
    list[tuple[str, pd.DataFrame]],
    str | None,
    pd.DataFrame | None,
    str | None,
]:
    """집계에 쓸 파일별 데이터와 행 맥락 라벨을 결정한다."""
    metric_aggregate = is_metric_aggregate_request(prompt, named_dfs=prepared)

    prompt_filtered = None
    prompt_label = None
    if not metric_aggregate:
        prompt_filtered = _filter_multi_by_mentioned_value(prepared, prompt)
        if prompt_filtered is not None and not prompt_filtered.empty:
            prompt_label = infer_context_label(
                prompt=prompt,
                result_df=prompt_filtered,
                full_df=None,
            ) or extract_matched_value(prompt_filtered, prompt)

    reuse_filter = (
        filter_df is not None
        and len(filter_df) > 0
        and "출처파일" in filter_df.columns
    )
    if reuse_filter and prompt_label and context_label:
        if _normalize_text(str(prompt_label)) != _normalize_text(str(context_label)):
            reuse_filter = False
    if reuse_filter and prompt_label and prompt_filtered is not None:
        on_filter = _filter_by_mentioned_value(filter_df, prompt)
        if on_filter is None or on_filter.empty:
            reuse_filter = False

    if reuse_filter:
        parts = split_frames_by_source(filter_df)
        if parts:
            if not context_label:
                context_label = infer_context_label(
                    prompt=None,
                    result_df=filter_df,
                    full_df=None,
                )
            return parts, str(context_label) if context_label else None, None, context_label

    if prompt_filtered is not None and not prompt_filtered.empty:
        label = prompt_label or infer_context_label(
            prompt=prompt,
            result_df=prompt_filtered,
            full_df=None,
        )
        if label:
            context_label = label
        return (
            split_frames_by_source(prompt_filtered),
            str(context_label) if context_label else None,
            prompt_filtered,
            label,
        )

    if not context_label:
        # 수치 컬럼 집계만이면 프롬프트 잔여 구('시트별숫자형컬럼비교' 등)를
        # 행 라벨로 쓰지 않는다. 필터 맥락이 있을 때만 라벨을 붙인다.
        if not metric_aggregate:
            context_label = infer_context_label(
                prompt=prompt, result_df=None, full_df=None
            )
    return prepared, str(context_label) if context_label else None, None, context_label
