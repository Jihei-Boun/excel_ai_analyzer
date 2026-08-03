"""프롬프트 라우팅·결과 후처리 (Streamlit 없음)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from core.aggregates import (
    build_context_aggregate_table,
    build_groupby_aggregate_table,
    build_multi_context_aggregate_table,
    scalar_to_context_table,
    split_frames_by_source,
)
from core.analyzer import run_analysis, run_multi_analysis
from core.chart_utils import generate_fallback_chart
from core.file_summary import (
    build_file_summary,
    build_multi_file_summary,
    is_summary_request,
)
from core.integrate_pipeline import looks_like_structural_integrate, try_integrate_pipeline
from core.prompt_intent import (
    _expects_plot,
    detect_aggregate_op,
    wants_table_and_chart,
)
from core.schema_compare import build_schema_outcome, is_schema_request
from core.quality import build_quality_outcome, is_quality_request
from core.result_format import exclude_aggregate_rows, restore_source_row_order, to_list_display
from core.text_normalize import _normalize_text
from core.value_filter import (
    _filter_by_mentioned_value,
    _filter_multi_by_mentioned_value,
    build_filter_summary,
    build_missing_rows_outcome,
    extract_matched_value,
    filter_missing_rows,
    infer_context_label,
    is_metric_aggregate_request,
    is_missing_rows_request,
)


@dataclass
class SingleRouteOutcome:
    reply: str
    dataframe: pd.DataFrame | None
    meta: dict = field(default_factory=dict)
    keep_as_filter: bool = False
    replace_selection: bool = True
    remember_aggregate: bool = False
    aggregate_prompt: str | None = None
    update_context_label: str | None = None
    update_filter_summary: str | None = None
    set_filter_df: pd.DataFrame | None = None
    clear_operation: bool = True
    set_operation_result: object | None = None
    operation_name: str | None = None
    reset_filter: bool = False
    filter_auto_reset: bool = False


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
            use_budget_profile=use_budget_profile,
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
            use_budget_profile=use_budget_profile,
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


def route_single_prompt(
    prompt: str,
    *,
    full_df: pd.DataFrame,
    source_df: pd.DataFrame,
    context_label: str | None,
    base_url: str,
    model: str,
    use_budget_profile: bool = False,
    prior_aggregate_df: pd.DataFrame | None = None,
    prior_aggregate_prompt: str | None = None,
    prior_user_prompt: str | None = None,
    last_assistant_df: pd.DataFrame | None = None,
    summary_text: str | None = None,
) -> SingleRouteOutcome:
    if is_summary_request(prompt):
        reply = summary_text
        if reply is None:
            reply = build_file_summary(full_df, use_budget_profile=use_budget_profile)
        return SingleRouteOutcome(reply=reply, dataframe=None)

    if is_missing_rows_request(prompt):
        reply, table = build_missing_rows_outcome(source_df, label="현재 데이터")
        return SingleRouteOutcome(
            reply=reply,
            dataframe=table,
            # 결측 행 조회는 미리보기용 — 이후 집계 범위를 잠그지 않는다
            keep_as_filter=False,
            replace_selection=False,
        )

    if is_quality_request(prompt):
        reply, table = build_quality_outcome(
            [("현재 데이터", full_df)],
            unit_label="대상",
            prompt=prompt,
        )
        return SingleRouteOutcome(
            reply=reply,
            dataframe=table,
            keep_as_filter=False,
            replace_selection=False,
        )

    if is_schema_request(prompt):
        reply, table = build_schema_outcome(
            prompt,
            [("현재 데이터", full_df)],
            unit_label="대상",
            use_budget_profile=use_budget_profile,
        )
        return SingleRouteOutcome(
            reply=reply,
            dataframe=table,
            keep_as_filter=False,
            replace_selection=False,
        )

    wants_plot = _expects_plot(prompt)
    wants_table = wants_table_and_chart(prompt)

    grouped = build_groupby_aggregate_table(
        source_df,
        prompt,
        use_budget_profile=use_budget_profile,
    )
    if grouped is not None:
        table, summary = grouped
        meta: dict = {}
        if wants_plot:
            chart_path = generate_fallback_chart(table, prompt)
            if chart_path:
                meta["chart_path"] = chart_path
        return SingleRouteOutcome(
            reply=summary,
            dataframe=table,
            meta=meta,
            keep_as_filter=False,
            replace_selection=False,
            remember_aggregate=True,
            aggregate_prompt=prompt,
        )

    if not wants_plot or wants_table:
        contextual = build_context_aggregate_table(
            source_df,
            prompt,
            context_label=context_label,
        )
        if contextual is not None:
            table, summary = contextual
            meta = {}
            if wants_plot:
                chart_path = generate_fallback_chart(table, prompt)
                if chart_path:
                    meta["chart_path"] = chart_path
            return SingleRouteOutcome(
                reply=summary,
                dataframe=table,
                meta=meta,
                keep_as_filter=False,
                replace_selection=False,
                remember_aggregate=True,
                aggregate_prompt=prompt,
            )

    if wants_plot and not wants_table:
        chart_table, chart_prompt = resolve_chart_table(
            source_df,
            prompt,
            context_label=context_label,
            use_budget_profile=use_budget_profile,
            prior_aggregate_df=prior_aggregate_df,
            prior_aggregate_prompt=prior_aggregate_prompt,
            last_assistant_df=last_assistant_df,
            prior_user_prompt=prior_user_prompt,
        )
        if chart_table is not None and not chart_table.empty:
            chart_path = generate_fallback_chart(chart_table, chart_prompt)
            if chart_path:
                return SingleRouteOutcome(
                    reply="차트 결과를 생성했습니다.",
                    dataframe=None,
                    meta={"chart_path": chart_path},
                )

    result, summary, analysis_meta = run_analysis(
        source_df,
        prompt,
        base_url=base_url,
        model=model,
        use_budget_profile=use_budget_profile,
        skip_aggregate_shortcuts=True,
    )

    reset_filter = False
    if (
        isinstance(result, pd.DataFrame)
        and result.empty
        and source_df is not full_df
        and len(full_df) > 0
    ):
        result, summary, analysis_meta = run_analysis(
            full_df,
            prompt,
            base_url=base_url,
            model=model,
            use_budget_profile=use_budget_profile,
            skip_aggregate_shortcuts=True,
        )
        if isinstance(result, pd.DataFrame) and not result.empty:
            reset_filter = True

    if analysis_meta.get("chart_path"):
        return SingleRouteOutcome(
            reply=summary or "차트 결과를 생성했습니다.",
            dataframe=None,
            meta=_merge_analysis_meta({}, analysis_meta),
        )

    if isinstance(result, pd.DataFrame):
        result = result.reset_index(drop=True)
        ctx_label, filter_summary = _context_updates_from_filter(full_df, prompt, result)
        result, summary, meta = postprocess_table_result(
            result,
            prompt,
            summary,
            source_df=source_df,
        )
        meta = _merge_analysis_meta(meta, analysis_meta)
        is_filter = detect_aggregate_op(prompt) is None
        if is_filter:
            meta, fs = _attach_filter_summary_meta(
                meta,
                prompt=prompt,
                result=result,
                full_df=full_df,
            )
            if fs and not filter_summary:
                filter_summary = fs
        return SingleRouteOutcome(
            reply=summary,
            dataframe=result,
            meta=meta,
            keep_as_filter=is_filter,
            replace_selection=True,
            update_context_label=ctx_label,
            update_filter_summary=filter_summary,
            reset_filter=reset_filter,
            filter_auto_reset=reset_filter,
        )

    meta = _merge_analysis_meta({}, analysis_meta)
    if meta.get("chart_path"):
        return SingleRouteOutcome(
            reply=summary or "차트 결과를 생성했습니다.",
            dataframe=None,
            meta=meta,
        )

    if detect_aggregate_op(prompt) is not None:
        table = scalar_to_context_table(
            result,
            prompt,
            source_df,
            context_label=context_label,
        )
        if table is not None:
            return SingleRouteOutcome(
                reply=summary or f"{context_label or '합계'} 집계 결과",
                dataframe=table,
                meta=meta,
                keep_as_filter=False,
                replace_selection=False,
            )

    # 긴 문자열 답변은 metric이 아니라 채팅 메시지로만 표시
    if isinstance(result, str):
        text = result.strip()
        return SingleRouteOutcome(
            reply=text or summary,
            dataframe=None,
            meta=meta,
        )

    return SingleRouteOutcome(
        reply=summary,
        dataframe=None,
        meta=meta,
        clear_operation=False,
        set_operation_result=result,
        operation_name="PandasAI",
    )


def route_multi_prompt(
    prompt: str,
    *,
    named_frames: list[tuple[str, pd.DataFrame]],
    base_url: str,
    model: str,
    use_budget_profile: bool = False,
    context_label: str | None,
    filter_df: pd.DataFrame | None,
    sheet_info: dict[str, dict] | None = None,
    unit_label: str = "파일",
) -> SingleRouteOutcome:
    prepared = named_frames

    if is_summary_request(prompt):
        reply = build_multi_file_summary(
            prepared,
            sheet_info=sheet_info,
            use_budget_profile=use_budget_profile,
            unit_label=unit_label,
        )
        return SingleRouteOutcome(reply=reply, dataframe=None)

    if is_missing_rows_request(prompt):
        parts: list[pd.DataFrame] = []
        for name, frame in prepared:
            missing = filter_missing_rows(frame)
            if missing.empty:
                continue
            part = missing.copy()
            part.insert(0, "출처파일", name)
            parts.append(part)
        if not parts:
            return SingleRouteOutcome(
                reply=f"선택된 {unit_label}에서 결측값이 있는 행을 찾지 못했습니다.",
                dataframe=None,
            )
        table = pd.concat(parts, ignore_index=True)
        reply = (
            f"결측값이 있는 행 {len(table):,}개 "
            f"({len(parts)}개 {unit_label})"
        )
        return SingleRouteOutcome(
            reply=reply,
            dataframe=table,
            keep_as_filter=False,
            replace_selection=False,
        )

    if is_quality_request(prompt):
        reply, table = build_quality_outcome(
            prepared,
            unit_label=unit_label,
            prompt=prompt,
        )
        return SingleRouteOutcome(
            reply=reply,
            dataframe=table,
            keep_as_filter=False,
            replace_selection=False,
        )

    if is_schema_request(prompt):
        reply, table = build_schema_outcome(
            prompt,
            prepared,
            unit_label=unit_label,
            use_budget_profile=use_budget_profile,
        )
        return SingleRouteOutcome(
            reply=reply,
            dataframe=table,
            keep_as_filter=False,
            replace_selection=False,
        )

    if detect_aggregate_op(prompt) is not None:
        source_named, agg_context, new_filter, new_label = resolve_multi_aggregate_source(
            prepared,
            prompt,
            context_label=context_label,
            filter_df=filter_df,
        )
        contextual = build_multi_context_aggregate_table(
            source_named,
            prompt,
            context_label=agg_context,
            unit_label=unit_label,
        )
        if contextual is not None:
            table, summary = contextual
            meta: dict = {}
            if _expects_plot(prompt):
                chart_path = generate_fallback_chart(table, prompt)
                if chart_path:
                    meta["chart_path"] = chart_path
                if not wants_table_and_chart(prompt):
                    return SingleRouteOutcome(
                        reply=summary or "차트 결과를 생성했습니다.",
                        dataframe=None,
                        meta=meta,
                        set_filter_df=new_filter,
                        update_context_label=new_label,
                    )
            return SingleRouteOutcome(
                reply=summary,
                dataframe=table,
                meta=meta,
                keep_as_filter=False,
                replace_selection=False,
                set_filter_df=new_filter,
                update_context_label=new_label,
            )

    # 범용 구조화 통합: LLM 스키마·계획 → 결정론 엔진 (도메인 전용 함수 호출 없음)
    if looks_like_structural_integrate(prompt) and len(prepared) >= 2:
        integrate_error: str | None = None
        integrated = None
        try:
            integrated = try_integrate_pipeline(
                prompt,
                prepared,
                base_url=base_url,
                model=model,
                use_budget_profile=use_budget_profile,
            )
        except Exception as exc:
            integrate_error = str(exc)
            integrated = None

        if integrated is not None and integrated.validation.ok:
            return SingleRouteOutcome(
                reply=integrated.reply,
                dataframe=integrated.integrated,
                meta=dict(integrated.meta),
                keep_as_filter=False,
                replace_selection=True,
                clear_operation=True,
                set_operation_result=integrated.integrated,
                operation_name="structured_integrate",
            )
        if integrated is not None and not integrated.validation.ok:
            # 재추론 후에도 검증 실패 → 잘못된 파일 확정 대신 경고 반환
            return SingleRouteOutcome(
                reply=(
                    "구조화 통합 계획을 실행했지만 검증에 실패했습니다. "
                    "잘못된 통합 파일은 저장하지 않았습니다. "
                    f"{integrated.validation.summary_text()}"
                ),
                dataframe=integrated.integrated,
                meta={
                    "integrate_plan": integrated.plan.to_dict(),
                    "integrate_validation": integrated.validation.summary_text(),
                },
                keep_as_filter=False,
                replace_selection=False,
                operation_name="structured_integrate_failed",
            )
        if integrate_error:
            return SingleRouteOutcome(
                reply=(
                    "구조화 통합 파이프라인 실행 중 오류가 발생했습니다. "
                    f"{integrate_error}"
                ),
                dataframe=None,
                keep_as_filter=False,
                replace_selection=False,
            )

    result, summary, analysis_meta = run_multi_analysis(
        prepared,
        prompt,
        base_url=base_url,
        model=model,
        use_budget_profile=use_budget_profile,
        skip_metric_aggregate=True,
    )

    if analysis_meta.get("chart_path"):
        return SingleRouteOutcome(
            reply=summary or "차트 결과를 생성했습니다.",
            dataframe=None,
            meta=_merge_analysis_meta({}, analysis_meta),
        )

    if isinstance(result, pd.DataFrame):
        result = result.reset_index(drop=True)
        is_filter = detect_aggregate_op(prompt) is None
        ctx_label: str | None = None
        filter_summary: str | None = None
        if is_filter:
            ctx_label, filter_summary = _context_updates_from_filter(result, prompt, result)

        multi_source = filter_df
        if multi_source is None or len(multi_source) == 0:
            parts = []
            for name, frame in prepared:
                part = frame.copy()
                part.insert(0, "출처파일", name)
                parts.append(part)
            multi_source = pd.concat(parts, ignore_index=True) if parts else None

        result, summary, meta = postprocess_table_result(
            result,
            prompt,
            summary,
            source_df=multi_source,
        )
        meta = _merge_analysis_meta(meta, analysis_meta)
        if is_filter:
            meta, fs = _attach_filter_summary_meta(
                meta,
                prompt=prompt,
                result=result,
                full_df=multi_source,
            )
            if fs and not filter_summary:
                filter_summary = fs
        return SingleRouteOutcome(
            reply=summary,
            dataframe=result,
            meta=meta,
            keep_as_filter=is_filter,
            replace_selection=True,
            update_context_label=ctx_label,
            update_filter_summary=filter_summary,
        )

    meta = _merge_analysis_meta({}, analysis_meta)
    if meta.get("chart_path"):
        return SingleRouteOutcome(
            reply=summary or "차트 결과를 생성했습니다.",
            dataframe=None,
            meta=meta,
        )

    # 긴 문자열 답변은 metric이 아니라 채팅 메시지로만 표시
    if isinstance(result, str):
        text = result.strip()
        return SingleRouteOutcome(
            reply=text or summary,
            dataframe=None,
            meta=meta,
        )

    return SingleRouteOutcome(
        reply=summary,
        dataframe=None,
        meta=meta,
        clear_operation=False,
        set_operation_result=result,
        operation_name="PandasAI (다중)",
    )
