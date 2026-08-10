"""자연어 요청 분석 진입점.

Phase 5 단일 파일 우선순위
--------------------------
1. System/Data Command
2. AnalysisPlan Pipeline (Planner → validate → execute → interpret)
3. Lightweight deterministic retrieval (value_match / list_seed)
4. legacy_simple_groupby_fallback (단순 X별 Y만)
5. PandasAI + code_guardrails

Legacy analytical shortcut(condition / context aggregate)은
single-file analytical path에서 제거한다.
"""

from __future__ import annotations

import pandas as pd

from core.aggregates import (
    _build_list_seed_frame,
    build_multi_context_aggregate_table,
)
from core.analysis.analysis_pipeline import try_analysis_pipeline
from core.analysis.legacy_fallback import try_legacy_simple_groupby_fallback
from core.display.chart_utils import generate_fallback_chart, generate_multi_file_chart
from core.pai.pandasai_config import chat, chat_multi
from core.routing.prompt_intent import (
    expects_plot,
    is_analytical_request,
    is_complex_analysis,
    is_list_request,
    resolve_output_type,
    wants_table_and_chart,
)
from core.schema.schema_compare import explain_column_meanings, is_column_meaning_request
from core.filter.value_filter import (
    _filter_by_mentioned_value,
    _filter_multi_by_mentioned_value,
    is_metric_aggregate_request,
    try_condition_row_filter,
)

__all__ = [
    "run_analysis",
    "run_multi_analysis",
]

# 차트 키워드와 함께 있으면 chart-only display가 아닌 분석+시각화로 본다.
# ('보여줘' 등 단순 표시 동사는 제외 — 차트 명령에 흔함)
_ANALYSIS_WITH_CHART_MARKERS = (
    "분석",
    "비교",
    "합계",
    "평균",
    "비율",
    "상위",
    "하위",
    "높은",
    "낮은",
    "대비",
    "차이",
    "효율",
    "순위",
    "찾아",
    "골라",
)


def _column_meaning_query(prompt: str, *, multi: bool = False) -> str:
    """하위 호환용 — 실제 의미 설명은 explain_column_meanings를 쓴다."""
    scope = (
        "제공된 모든 DataFrame의 컬럼을 대상으로 합니다.\n"
        if multi
        else "현재 DataFrame의 컬럼을 대상으로 합니다.\n"
    )
    return (
        "컬럼명과 실제 데이터 샘플(dtype·고유값·예시 값)을 보고 "
        "각 컬럼이 무엇을 의미하는지 추정해 설명하세요.\n"
        f"{scope}"
        "pandas로 집계·필터·정렬을 하지 마세요.\n"
        "result type은 string으로 두고, 컬럼별로 의미를 자연어로 작성하세요.\n"
        "확신이 낮으면 추정임을 명시하세요.\n"
        f"사용자 요청: {prompt}"
    )


def _is_chart_only_display(prompt: str) -> bool:
    """명확한 차트 명령만 True. 분석+시각화는 False → Planner 우선."""
    if not expects_plot(prompt):
        return False
    if wants_table_and_chart(prompt):
        return False
    return not any(m in prompt for m in _ANALYSIS_WITH_CHART_MARKERS)


def run_analysis(
    df: pd.DataFrame,
    prompt: str,
    *,
    base_url: str,
    model: str,
    profile_name: str | None = None,
    skip_aggregate_shortcuts: bool = False,
) -> tuple[object, str, dict]:
    """DataFrame과 사용자 요청을 PandasAI에 전달해 결과를 반환한다."""
    from core.profile_loader import resolve_profile_name, use_profile

    name = resolve_profile_name(
        profile_name=profile_name,
    )
    with use_profile(name):
        return _run_analysis_impl(
            df,
            prompt,
            base_url=base_url,
            model=model,
            profile_name=name,
            skip_aggregate_shortcuts=skip_aggregate_shortcuts,
        )


def _run_analysis_impl(
    df: pd.DataFrame,
    prompt: str,
    *,
    base_url: str,
    model: str,
    profile_name: str | None = None,
    skip_aggregate_shortcuts: bool = False,
) -> tuple[object, str, dict]:
    """run_analysis 본문 (활성 프로필 컨텍스트 안에서 실행)."""
    if not prompt.strip():
        raise ValueError("분석 요청을 입력해 주세요.")

    # ------------------------------------------------------------------
    # A. System/Data Command
    # ------------------------------------------------------------------
    if is_column_meaning_request(prompt):
        text = explain_column_meanings(
            df,
            prompt,
            base_url=base_url,
            model=model,
            profile_name=profile_name,
        )
        return None, text, {}

    output_type = resolve_output_type(prompt)

    from core.summary.file_summary import build_file_summary, is_summary_request

    if is_summary_request(prompt):
        summary = build_file_summary(df, profile_name=profile_name)
        return None, summary, {}

    # Chart-only display (분석 방법을 우회하지 않음)
    if _is_chart_only_display(prompt):
        chart_path = generate_fallback_chart(df, prompt)
        if chart_path:
            return None, "차트 결과를 생성했습니다.", {"chart_path": chart_path}

    # ------------------------------------------------------------------
    # AnalysisPlan — analytical request면 키워드와 무관하게 우선 시도
    # ------------------------------------------------------------------
    pipeline_exhaust: dict = {}
    if is_analytical_request(prompt, profile_name=profile_name):
        planned = try_analysis_pipeline(
            prompt,
            df,
            base_url=base_url,
            model=model,
            profile_name=profile_name,
            exhaust_meta=pipeline_exhaust,
        )
        if planned is not None:
            meta = dict(planned.meta)
            # 분석+차트: Planner 결과 위에 렌더 (분석 우회 금지)
            if expects_plot(prompt) and not meta.get("chart_path"):
                chart_source = (
                    planned.dataframe
                    if isinstance(planned.dataframe, pd.DataFrame)
                    and not planned.dataframe.empty
                    else df
                )
                chart_path = generate_fallback_chart(chart_source, prompt)
                if chart_path:
                    meta["chart_path"] = chart_path
            return planned.dataframe, planned.reply, meta

    prior_reason = str(pipeline_exhaust.get("fallback_reason") or "")

    # Expected-impossible requests: end as safe_failure without PandasAI (Phase 12G).
    # Conservative: only when planner exhausted on clear missing-column / invent signals.
    if prior_reason in {"plan_validation_exhausted", "planner_generation_failed"}:
        safe = _maybe_safe_plan_failure(pipeline_exhaust, prompt)
        if safe is not None:
            return safe

    # ------------------------------------------------------------------
    # Lightweight deterministic retrieval (exact value / list)
    # ------------------------------------------------------------------
    if output_type != "plot" and not is_complex_analysis(
        prompt, profile_name=profile_name
    ):
        direct = _filter_by_mentioned_value(df, prompt)
        if direct is not None and not direct.empty:
            return (
                direct,
                f"데이터 값 일치 결과: {len(direct):,}행",
                {
                    "aggregation": {"operation": "value_match"},
                    "fallback_reason": "retrieval_fallback",
                    "prior_pipeline_reason": prior_reason or None,
                },
            )

    if output_type != "plot" and is_list_request(prompt):
        seed = _build_list_seed_frame(df, prompt)
        if seed is not None and not seed.empty:
            return (
                seed,
                f"리스트 결과: {len(seed):,}행",
                {
                    "aggregation": {"operation": "list_seed"},
                    "fallback_reason": "retrieval_fallback",
                    "prior_pipeline_reason": prior_reason or None,
                },
            )

    # ------------------------------------------------------------------
    # legacy_simple_groupby_fallback (Planner exhausted 후 단순 X별 Y만)
    # ------------------------------------------------------------------
    if output_type != "plot" and not skip_aggregate_shortcuts:
        grouped = try_legacy_simple_groupby_fallback(
            df,
            prompt,
            profile_name=profile_name,
        )
        if grouped is not None:
            table, summary = grouped
            return (
                table,
                summary,
                {
                    "aggregation": {
                        "operation": "legacy_simple_groupby_fallback",
                    },
                    "fallback_reason": "simple_groupby_fallback",
                    "prior_pipeline_reason": prior_reason or None,
                    "retry_log": list(pipeline_exhaust.get("retry_log") or []),
                },
            )

    # ------------------------------------------------------------------
    # PandasAI — 최후 fallback (code_guardrails는 chat() 내부)
    # ------------------------------------------------------------------
    from core.profile_loader import dataframe_request_hint_for

    df_hint = dataframe_request_hint_for(profile_name=profile_name) or (
        "'list', 'table', 'show' requests must return a DataFrame."
    )
    query = (
        "사용자의 요청을 현재 DataFrame의 실제 컬럼명과 데이터 타입에 맞춰 "
        "pandas 연산으로 수행하세요.\n"
        "필터링, 정렬, 집계, 그룹화, 피벗, 통계 등 요청된 종류를 스스로 판단하세요.\n"
        "특정 컬럼이나 데이터 형식을 가정하지 마세요.\n"
        "스키마 힌트는 참고용이며, 컬럼을 임의로 rewrite하지 마세요.\n"
        "반복된 상위 분류 값은 분석용 복사본에서만 채운 값일 수 있으므로 "
        "같은 분류의 모든 행을 필터링할 때 사용하세요.\n"
        "항목을 찾는 요청은 식별·조건 확인에 필요한 최소 컬럼만 남기세요. "
        "원본의 모든 열을 그대로 반환하지 마세요.\n"
        f"{df_hint}\n"
        "차트·그래프·시각화 요청은 plot type으로 차트를 저장하고 경로를 반환하세요.\n"
        "합계·총합·평균 등 집계 요청도 가능하면 "
        "행 라벨(분류명)과 컬럼명·집계값으로 된 작은 DataFrame으로 반환하세요.\n"
        "예: 열이 ['', '매출']이고 행이 ['상품A', 12345] 형태.\n"
        "그 외 단일 계산만 숫자나 문자열로 반환하세요.\n"
        f"사용자 요청: {prompt}"
    )
    try:
        result, summary, meta = chat(
            df,
            query,
            base_url=base_url,
            model=model,
            output_type=output_type,
        )
        meta = {
            **dict(meta or {}),
            "fallback_reason": "pandasai_final_fallback",
            "prior_pipeline_reason": prior_reason or None,
            "retry_log": list(pipeline_exhaust.get("retry_log") or []),
        }
    except RuntimeError:
        if output_type == "plot":
            chart_path = generate_fallback_chart(df, prompt)
            if chart_path:
                return None, "차트 결과를 생성했습니다.", {
                    "chart_path": chart_path,
                    "fallback_reason": "pandasai_final_fallback",
                }
        if output_type == "dataframe":
            fallback = _filter_by_mentioned_value(df, prompt)
            if fallback is not None and not fallback.empty:
                return (
                    fallback,
                    f"데이터 값 일치 결과: {len(fallback):,}행",
                    {
                        "fallback_reason": "retrieval_fallback",
                        "prior_pipeline_reason": prior_reason or None,
                    },
                )
        raise

    if (
        output_type == "dataframe"
        and isinstance(result, pd.DataFrame)
        and result.empty
    ):
        fallback = _filter_by_mentioned_value(df, prompt)
        if fallback is not None and not fallback.empty:
            return (
                fallback,
                f"데이터 값 일치 결과: {len(fallback):,}행",
                {},
            )

    if output_type == "plot" and not meta.get("chart_path"):
        chart_source = result if isinstance(result, pd.DataFrame) and not result.empty else df
        chart_path = generate_fallback_chart(chart_source, prompt)
        if chart_path:
            meta = {**meta, "chart_path": chart_path}
            summary = "차트 결과를 생성했습니다."

    return result, summary, meta


def _maybe_safe_plan_failure(
    pipeline_exhaust: dict,
    prompt: str,
) -> tuple[pd.DataFrame, str, dict] | None:
    """명확히 불가능한 요청은 PandasAI로 넘기지 않고 safe_failure로 종료.

    UX를 크게 바꾸지 않도록 missing/invented column 신호에만 보수적으로 적용.
    """
    retry_log = list(pipeline_exhaust.get("retry_log") or [])
    if not retry_log:
        return None
    joined = " ".join(
        " ".join(str(x) for x in (r.get("validation_errors") or [])) for r in retry_log
    ).lower()
    missing_signals = (
        "missing_column",
        "unknown_column",
        "invent",
        "존재하지 않는",
        "not in",
        "invalid_column",
    )
    if not any(sig in joined for sig in missing_signals):
        return None
    # Avoid treating recoverable composition errors as safe failure
    if "column_vs_column" in joined or "entity_ranking" in joined or "missing_ratio" in joined:
        return None
    return (
        pd.DataFrame(),
        "요청하신 분석을 현재 데이터 스키마로는 수행할 수 없습니다. "
        "컬럼명·조건을 확인한 뒤 다시 질문해 주세요.",
        {
            "aggregation": {"operation": "safe_failure"},
            "fallback_reason": "safe_plan_failure",
            "prior_pipeline_reason": pipeline_exhaust.get("fallback_reason"),
            "retry_log": retry_log,
            "source": "analysis_plan_safe_failure",
        },
    )


def run_multi_analysis(
    named_dfs: list[tuple[str, pd.DataFrame]],
    prompt: str,
    *,
    base_url: str,
    model: str,
    profile_name: str | None = None,
    skip_metric_aggregate: bool = False,
) -> tuple[object, str, dict]:
    """여러 DataFrame을 SmartDatalake로 동시에 분석한다."""
    from core.profile_loader import resolve_profile_name, use_profile

    name = resolve_profile_name(
        profile_name=profile_name,
    )
    with use_profile(name):
        return _run_multi_analysis_impl(
            named_dfs,
            prompt,
            base_url=base_url,
            model=model,
            profile_name=name,
            skip_metric_aggregate=skip_metric_aggregate,
        )


def _run_multi_analysis_impl(
    named_dfs: list[tuple[str, pd.DataFrame]],
    prompt: str,
    *,
    base_url: str,
    model: str,
    profile_name: str | None = None,
    skip_metric_aggregate: bool = False,
) -> tuple[object, str, dict]:
    """run_multi_analysis 본문 (활성 프로필 컨텍스트 안에서 실행).

    multi-file Planner는 Phase 6+ 범위. 기존 multi fallback은 유지한다.
    """
    if len(named_dfs) < 2:
        raise ValueError("동시 분석에는 파일 2개 이상이 필요합니다.")
    if not prompt.strip():
        raise ValueError("분석 요청을 입력해 주세요.")

    if is_column_meaning_request(prompt):
        parts: list[str] = []
        for name, frame in named_dfs:
            block = explain_column_meanings(
                frame,
                prompt,
                base_url=base_url,
                model=model,
                profile_name=profile_name,
            )
            parts.append(f"### {name}\n{block}")
        return None, "\n\n".join(parts), {}

    output_type = resolve_output_type(prompt)

    if output_type == "plot":
        chart_path = generate_multi_file_chart(named_dfs, prompt)
        if chart_path:
            return None, "차트 결과를 생성했습니다.", {"chart_path": chart_path}

    from core.summary.file_summary import build_multi_file_summary, is_summary_request

    if is_summary_request(prompt):
        summary = build_multi_file_summary(
            named_dfs,
            profile_name=profile_name,
        )
        return None, summary, {}

    metric_aggregate = is_metric_aggregate_request(prompt, named_dfs=named_dfs)
    if output_type == "dataframe" and metric_aggregate and not skip_metric_aggregate:
        contextual = build_multi_context_aggregate_table(named_dfs, prompt)
        if contextual is not None:
            table, summary = contextual
            return table, summary, {}

    if output_type == "dataframe":
        cond_parts: list[pd.DataFrame] = []
        conditioned_any = False
        for name, frame in named_dfs:
            part = try_condition_row_filter(
                frame, prompt, profile_name=profile_name
            )
            if part is None:
                continue
            conditioned_any = True
            if part.empty:
                continue
            tagged = part.copy()
            tagged.insert(0, "출처파일", name)
            cond_parts.append(tagged)
        if conditioned_any:
            if cond_parts:
                merged = pd.concat(cond_parts, ignore_index=True)
                file_count = (
                    merged["출처파일"].nunique()
                    if "출처파일" in merged.columns
                    else len(cond_parts)
                )
                return (
                    merged,
                    f"조건 필터 결과: {len(merged):,}행 ({file_count}개 파일)",
                    {},
                )
            empty = named_dfs[0][1].iloc[0:0].copy()
            return empty, "조건 필터 결과: 0행", {}

    if output_type == "dataframe" and not is_complex_analysis(
        prompt, profile_name=profile_name
    ):
        direct = _filter_multi_by_mentioned_value(named_dfs, prompt)
        if direct is not None and not direct.empty:
            file_count = (
                direct["출처파일"].nunique()
                if "출처파일" in direct.columns
                else len(named_dfs)
            )
            return (
                direct,
                f"데이터 값 일치 결과: {len(direct):,}행 ({file_count}개 파일)",
                {"aggregation": {"operation": "value_match"}},
            )

    if output_type == "dataframe" and is_list_request(prompt):
        list_parts: list[pd.DataFrame] = []
        for name, frame in named_dfs:
            seed = _build_list_seed_frame(frame, prompt)
            if seed is None or seed.empty:
                continue
            part = seed.copy()
            part.insert(0, "출처파일", name)
            list_parts.append(part)
        if list_parts:
            merged = pd.concat(list_parts, ignore_index=True)
            file_count = merged["출처파일"].nunique() if "출처파일" in merged.columns else 0
            return merged, f"리스트 결과: {len(merged):,}행 ({file_count}개 파일)", {}

    from core.profile_loader import dataframe_request_hint_for

    file_names = ", ".join(name for name, _ in named_dfs)
    df_hint = dataframe_request_hint_for(profile_name=profile_name) or (
        "'list', 'table', 'show' requests must return a DataFrame."
    )
    query = (
        "여러 엑셀 파일의 DataFrame이 동시에 제공됩니다. "
        "각 dfs[i]는 서로 다른 파일이며, 파일명과 테이블명을 참고하세요.\n"
        "비교, 병합(merge/join), 교차 집계, 공통 컬럼 탐색 등 요청을 판단해 pandas로 수행하세요.\n"
        "특정 컬럼이나 데이터 형식을 가정하지 마세요.\n"
        "반복된 상위 분류 값은 원본의 빈 상세 행을 분석용으로 채운 값이므로 "
        "같은 분류의 모든 행을 필터링할 때 사용하세요.\n"
        f"{df_hint}\n"
        "차트·그래프·시각화 요청은 plot type으로 차트를 저장하고 경로를 반환하세요.\n"
        "단일 계산만 숫자나 문자열로 반환하세요.\n"
        f"분석 대상 파일: {file_names}\n"
        f"사용자 요청: {prompt}"
    )
    try:
        result, summary, meta = chat_multi(
            named_dfs,
            query,
            base_url=base_url,
            model=model,
            output_type=output_type,
        )
    except RuntimeError:
        if output_type == "plot" and named_dfs:
            chart_path = generate_multi_file_chart(named_dfs, prompt)
            if chart_path:
                return None, "차트 결과를 생성했습니다.", {"chart_path": chart_path}
        if output_type == "dataframe" and not metric_aggregate:
            fallback = _filter_multi_by_mentioned_value(named_dfs, prompt)
            if fallback is not None and not fallback.empty:
                file_count = (
                    fallback["출처파일"].nunique()
                    if "출처파일" in fallback.columns
                    else len(named_dfs)
                )
                return (
                    fallback,
                    f"데이터 값 일치 결과: {len(fallback):,}행 ({file_count}개 파일)",
                    {},
                )
        if output_type == "dataframe" and metric_aggregate:
            contextual = build_multi_context_aggregate_table(named_dfs, prompt)
            if contextual is not None:
                table, summary = contextual
                return table, summary, {}
        raise

    if (
        output_type == "dataframe"
        and isinstance(result, pd.DataFrame)
        and result.empty
    ):
        if metric_aggregate:
            contextual = build_multi_context_aggregate_table(named_dfs, prompt)
            if contextual is not None:
                table, summary = contextual
                return table, summary, {}
        fallback = _filter_multi_by_mentioned_value(named_dfs, prompt)
        if fallback is not None and not fallback.empty:
            file_count = (
                fallback["출처파일"].nunique()
                if "출처파일" in fallback.columns
                else len(named_dfs)
            )
            return (
                fallback,
                f"데이터 값 일치 결과: {len(fallback):,}행 ({file_count}개 파일)",
                {},
            )

    if output_type == "plot" and not meta.get("chart_path") and named_dfs:
        if isinstance(result, pd.DataFrame) and not result.empty:
            chart_path = generate_fallback_chart(result, prompt)
        else:
            chart_path = None
        if not chart_path:
            chart_path = generate_multi_file_chart(named_dfs, prompt)
        if chart_path:
            meta = {**meta, "chart_path": chart_path}
            summary = "차트 결과를 생성했습니다."

    return result, summary, meta
