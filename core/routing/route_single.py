"""단일 파일/시트 프롬프트 라우팅.

Phase 5 우선순위
----------------
1. 질문 종류(LLM 판정) → 요약·빈 칸 행·품질·구조·열 의미는 정해진 처리기
2. Chart-only display (분석 우회 없음; 표+차트는 아래)
3. Analysis Pipeline (run_analysis: Planner → retrieval → simple groupby → PandasAI)

Router는 비교/비율/순위 등 분석 *방법*을 고르지 않는다.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd

from core.aggregates import scalar_to_context_table
from core.analysis.analyzer import run_analysis
from core.display.chart_utils import generate_fallback_chart
from core.summary.file_summary import build_file_summary
from core.routing.prompt_intent import (
    _expects_plot,
    detect_aggregate_op,
    wants_table_and_chart,
)
from core.schema.quality import build_quality_outcome
from core.routing.request_kind import (
    KIND_ANALYSIS,
    KIND_COLUMN_MEANING,
    KIND_MISSING_ROWS,
    KIND_QUALITY,
    KIND_SCHEMA,
    KIND_SUMMARY,
    classify_request,
)
from core.routing.route_helpers import (
    _attach_filter_summary_meta,
    _context_updates_from_filter,
    _merge_analysis_meta,
    postprocess_table_result,
    resolve_chart_table,
)
from core.routing.route_types import SingleRouteOutcome
from core.schema.schema_compare import build_schema_outcome, explain_column_meanings
from core.filter.value_filter import build_missing_rows_outcome


def route_single_prompt(
    prompt: str,
    *,
    full_df: pd.DataFrame,
    source_df: pd.DataFrame,
    context_label: str | None,
    base_url: str,
    model: str,
    profile_name: str | None = None,
    prior_aggregate_df: pd.DataFrame | None = None,
    prior_aggregate_prompt: str | None = None,
    prior_user_prompt: str | None = None,
    last_assistant_df: pd.DataFrame | None = None,
    build_summary: Callable[[], str] | None = None,
    request_kind: str | None = None,
) -> SingleRouteOutcome:
    # ------------------------------------------------------------------
    # A. 질문 종류 — LLM이 처리기를 고른다 (request_kind.py)
    #    요약·빈 칸 행·품질·구조·열 의미는 Planner 없이 정해진 처리기로 답한다.
    # ------------------------------------------------------------------
    kind = request_kind or classify_request(prompt, full_df, base_url=base_url, model=model)
    system = _run_system_kind(
        kind,
        prompt,
        full_df=full_df,
        source_df=source_df,
        base_url=base_url,
        model=model,
        profile_name=profile_name,
        build_summary=build_summary,
    )
    if system is not None:
        system.meta = {**(system.meta or {}), "request_kind": kind}
        return system

    outcome = _route_analysis(
        prompt,
        full_df=full_df,
        source_df=source_df,
        context_label=context_label,
        base_url=base_url,
        model=model,
        profile_name=profile_name,
        prior_aggregate_df=prior_aggregate_df,
        prior_aggregate_prompt=prior_aggregate_prompt,
        prior_user_prompt=prior_user_prompt,
        last_assistant_df=last_assistant_df,
    )
    outcome.meta = {**(outcome.meta or {}), "request_kind": KIND_ANALYSIS}
    return outcome


def _run_system_kind(
    kind: str,
    prompt: str,
    *,
    full_df: pd.DataFrame,
    source_df: pd.DataFrame,
    base_url: str,
    model: str,
    profile_name: str | None,
    build_summary: Callable[[], str] | None,
) -> SingleRouteOutcome | None:
    """분석이 아닌 종류를 정해진 처리기로 답한다. 분석이면 None."""
    from core.profile_loader import schema_ui_for

    if kind == KIND_SUMMARY:
        reply = build_summary() if build_summary else build_file_summary(
            full_df, profile_name=profile_name
        )
        return SingleRouteOutcome(reply=reply, dataframe=None)

    if kind == KIND_COLUMN_MEANING:
        reply = explain_column_meanings(
            full_df, prompt, base_url=base_url, model=model, profile_name=profile_name
        )
        return SingleRouteOutcome(reply=reply, dataframe=None)

    ui = schema_ui_for(profile_name=profile_name)
    if kind == KIND_MISSING_ROWS:
        reply, table = build_missing_rows_outcome(
            source_df,
            label=ui["current_data"],
            profile_name=profile_name,
        )
    elif kind == KIND_QUALITY:
        reply, table = build_quality_outcome(
            [(ui["current_data"], full_df)],
            unit_label=ui["unit_target"],
            prompt=prompt,
        )
    elif kind == KIND_SCHEMA:
        reply, table = build_schema_outcome(
            prompt,
            [(ui["current_data"], full_df)],
            unit_label=ui["unit_target"],
            profile_name=profile_name,
        )
    else:
        return None
    # 빈 칸 행·품질·구조 표는 미리보기용 — 이후 집계 범위를 잠그지 않는다
    return SingleRouteOutcome(
        reply=reply,
        dataframe=table,
        keep_as_filter=False,
        replace_selection=False,
    )


def _route_analysis(
    prompt: str,
    *,
    full_df: pd.DataFrame,
    source_df: pd.DataFrame,
    context_label: str | None,
    base_url: str,
    model: str,
    profile_name: str | None,
    prior_aggregate_df: pd.DataFrame | None,
    prior_aggregate_prompt: str | None,
    prior_user_prompt: str | None,
    last_assistant_df: pd.DataFrame | None,
) -> SingleRouteOutcome:
    wants_plot = _expects_plot(prompt)
    wants_table = wants_table_and_chart(prompt)

    # ------------------------------------------------------------------
    # C. Chart-only display fallback (분석 방법 결정 아님)
    #    표+차트 요청은 아래 Analysis Pipeline으로 보낸다.
    # ------------------------------------------------------------------
    if wants_plot and not wants_table:
        chart_table, chart_prompt = resolve_chart_table(
            source_df,
            prompt,
            context_label=context_label,
            profile_name=profile_name,
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

    # ------------------------------------------------------------------
    # Analytical request → Analysis Pipeline
    #   내부: Planner → retrieval → legacy_simple_groupby → PandasAI
    # ------------------------------------------------------------------
    result, summary, analysis_meta = run_analysis(
        source_df,
        prompt,
        base_url=base_url,
        model=model,
        profile_name=profile_name,
        skip_aggregate_shortcuts=False,
    )
    if analysis_meta.get("engine") == "step":
        # 단계별 엔진의 답은 그대로 보여준다. 아래 후처리(합계 행 빼기, 원본 순서 복원,
        # 빈 결과면 전체 표로 다시 묻기)는 질문 단어로 판단하는 옛 경로용이라 거치지 않는다.
        # 답 표가 다음 질문의 분석 범위가 되지도 않는다.
        return SingleRouteOutcome(
            reply=summary,
            dataframe=result,
            meta=analysis_meta,
            keep_as_filter=False,
            replace_selection=False,
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
            profile_name=profile_name,
            skip_aggregate_shortcuts=False,
        )
        if isinstance(result, pd.DataFrame) and not result.empty:
            reset_filter = True

    if analysis_meta.get("chart_path"):
        return SingleRouteOutcome(
            reply=summary or "차트 결과를 생성했습니다.",
            dataframe=None,
            meta=_merge_analysis_meta({}, analysis_meta),
        )

    aggregation = analysis_meta.get("aggregation") or {}
    agg_op = aggregation.get("operation")
    is_legacy_aggregate = agg_op in {
        "groupby",
        "legacy_simple_groupby_fallback",
        "context_aggregate",
        "context",
    }
    is_analysis_plan = agg_op == "analysis_plan"

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
        # 구조화 분석·레거시 집계 결과는 필터 잠금/비목 일치 배지 대상이 아니다.
        if (
            analysis_meta.get("comparison")
            or analysis_meta.get("aggregate_sources")
            or analysis_meta.get("correlation")
            or analysis_meta.get("vs_mean")
            or is_analysis_plan
            or is_legacy_aggregate
        ):
            is_filter = False
            ctx_label = None
            filter_summary = None
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
            # 투영/집계 결과표를 다음 질문 분석 범위로 남기지 않는다.
            replace_selection=bool(is_filter),
            update_context_label=ctx_label if is_filter else None,
            update_filter_summary=filter_summary if is_filter else None,
            reset_filter=reset_filter,
            filter_auto_reset=reset_filter,
            remember_aggregate=is_legacy_aggregate,
            aggregate_prompt=prompt if is_legacy_aggregate else None,
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
