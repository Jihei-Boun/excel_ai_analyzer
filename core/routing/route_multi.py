"""다중 파일/시트 프롬프트 라우팅.

Phase 2 범위: multi-file AnalysisPlan은 추가하지 않는다.
System/Data Command는 single과 동일하게 deterministic 유지.
집계·integrate·PandasAI는 기존 흐름 유지 (향후 Planner 확장 지점).

Phase 37: optional Shadow observation of the frozen Integration Pipeline.
Shadow never replaces the user-facing SingleRouteOutcome (kill switch OFF by default).
"""

from __future__ import annotations

import time

import pandas as pd

from core.aggregates import build_multi_context_aggregate_table
from core.analysis.analyzer import run_multi_analysis
from core.display.chart_utils import generate_fallback_chart
from core.summary.file_summary import (
    build_multi_file_summary,
    is_summary_request,
)
from core.integrate.integrate_pipeline import looks_like_structural_integrate, try_integrate_pipeline
from core.routing.prompt_intent import (
    _expects_plot,
    detect_aggregate_op,
    wants_table_and_chart,
)
from core.schema.quality import build_quality_outcome, is_quality_request
from core.routing.route_helpers import (
    _attach_filter_summary_meta,
    _context_updates_from_filter,
    _merge_analysis_meta,
    postprocess_table_result,
    resolve_multi_aggregate_source,
)
from core.routing.route_types import SingleRouteOutcome
from core.schema.schema_compare import build_schema_outcome, is_schema_request
from core.filter.value_filter import (
    filter_missing_rows,
    is_missing_rows_request,
)
from core.shadow.hook import (
    finish_with_shadow,
    maybe_build_shadow_snapshot,
    observe_exception_with_shadow,
)


def route_multi_prompt(
    prompt: str,
    *,
    named_frames: list[tuple[str, pd.DataFrame]],
    base_url: str,
    model: str,
    profile_name: str | None = None,
    context_label: str | None,
    filter_df: pd.DataFrame | None,
    sheet_info: dict[str, dict] | None = None,
    unit_label: str = "파일",
    request_id: str | None = None,
    case_id: str | None = None,
) -> SingleRouteOutcome:
    prepared = named_frames

    # A. System/Data Command (single과 동일 — Planner 불필요)
    # Shadow는 LLM/integrate 경로만 관찰 (system command는 대상 아님)
    if is_summary_request(prompt):
        reply = build_multi_file_summary(
            prepared,
            sheet_info=sheet_info,
            profile_name=profile_name,
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
            profile_name=profile_name,
        )
        return SingleRouteOutcome(
            reply=reply,
            dataframe=table,
            keep_as_filter=False,
            replace_selection=False,
        )

    # Phase 37: snapshot before legacy LLM/integrate work (enabled via env only)
    # Phase 38: post-snapshot uncaught legacy exceptions still schedule Shadow once.
    legacy_t0 = time.time()
    shadow_snap = maybe_build_shadow_snapshot(
        prompt=prompt,
        named_frames=prepared,
        base_url=base_url,
        model=model,
        profile_name=profile_name,
        request_id=request_id,
        case_id=case_id,
    )
    shadow_scheduled = False

    def _finish(outcome: SingleRouteOutcome) -> SingleRouteOutcome:
        nonlocal shadow_scheduled
        if shadow_scheduled:
            return outcome
        out = finish_with_shadow(
            outcome,
            snapshot=shadow_snap,
            legacy_started_at=legacy_t0,
        )
        if shadow_snap is not None:
            shadow_scheduled = True
        return out

    try:

        # B. Legacy multi aggregate (Phase 2: multi Planner 미구현 — 기존 유지)
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
                        return _finish(
                            SingleRouteOutcome(
                                reply=summary or "차트 결과를 생성했습니다.",
                                dataframe=None,
                                meta=meta,
                                set_filter_df=new_filter,
                                update_context_label=new_label,
                            )
                        )
                return _finish(
                    SingleRouteOutcome(
                        reply=summary,
                        dataframe=table,
                        meta=meta,
                        keep_as_filter=False,
                        replace_selection=False,
                        set_filter_df=new_filter,
                        update_context_label=new_label,
                    )
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
                    profile_name=profile_name,
                )
            except Exception as exc:
                integrate_error = str(exc)
                integrated = None

            if integrated is not None and integrated.validation.ok:
                return _finish(
                    SingleRouteOutcome(
                        reply=integrated.reply,
                        dataframe=integrated.integrated,
                        meta=dict(integrated.meta),
                        keep_as_filter=False,
                        replace_selection=True,
                        clear_operation=True,
                        set_operation_result=integrated.integrated,
                        operation_name="structured_integrate",
                    )
                )
            if integrated is not None and not integrated.validation.ok:
                # 재추론 후에도 검증 실패 → 잘못된 파일 확정 대신 경고 반환
                return _finish(
                    SingleRouteOutcome(
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
                )
            if integrate_error:
                return _finish(
                    SingleRouteOutcome(
                        reply=(
                            "구조화 통합 파이프라인 실행 중 오류가 발생했습니다. "
                            f"{integrate_error}"
                        ),
                        dataframe=None,
                        keep_as_filter=False,
                        replace_selection=False,
                    )
                )

        result, summary, analysis_meta = run_multi_analysis(
            prepared,
            prompt,
            base_url=base_url,
            model=model,
            profile_name=profile_name,
            skip_metric_aggregate=True,
        )

        if analysis_meta.get("chart_path"):
            return _finish(
                SingleRouteOutcome(
                    reply=summary or "차트 결과를 생성했습니다.",
                    dataframe=None,
                    meta=_merge_analysis_meta({}, analysis_meta),
                )
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
            return _finish(
                SingleRouteOutcome(
                    reply=summary,
                    dataframe=result,
                    meta=meta,
                    keep_as_filter=is_filter,
                    replace_selection=True,
                    update_context_label=ctx_label,
                    update_filter_summary=filter_summary,
                )
            )

        meta = _merge_analysis_meta({}, analysis_meta)
        if meta.get("chart_path"):
            return _finish(
                SingleRouteOutcome(
                    reply=summary or "차트 결과를 생성했습니다.",
                    dataframe=None,
                    meta=meta,
                )
            )

        # 긴 문자열 답변은 metric이 아니라 채팅 메시지로만 표시
        if isinstance(result, str):
            text = result.strip()
            return _finish(
                SingleRouteOutcome(
                    reply=text or summary,
                    dataframe=None,
                    meta=meta,
                )
            )

        return _finish(
            SingleRouteOutcome(
                reply=summary,
                dataframe=None,
                meta=meta,
                clear_operation=False,
                set_operation_result=result,
                operation_name="PandasAI (다중)",
            )
        )
    except Exception as exc:
        # Observational only: schedule then re-raise unchanged (traceback preserved).
        # Uses Exception (not BaseException) so KeyboardInterrupt/SystemExit skip Shadow.
        if shadow_snap is not None and not shadow_scheduled:
            observe_exception_with_shadow(
                exc,
                snapshot=shadow_snap,
                legacy_started_at=legacy_t0,
            )
            shadow_scheduled = True
        raise
