"""자연어 요청을 PandasAI로 실행하는 범용 분석 진입점."""

from __future__ import annotations

import pandas as pd

from core.aggregates import (
    _build_list_seed_frame,
    build_groupby_aggregate_table,
    build_multi_context_aggregate_table,
)
from core.analysis_pipeline import try_analysis_pipeline
from core.chart_utils import generate_fallback_chart, generate_multi_file_chart
from core.pandasai_config import chat, chat_multi
from core.prompt_intent import (
    is_complex_analysis,
    is_list_request,
    resolve_output_type,
    wants_structured_analysis,
)
from core.schema_compare import is_column_meaning_request
from core.value_filter import (
    _filter_by_mentioned_value,
    _filter_multi_by_mentioned_value,
    is_metric_aggregate_request,
    try_condition_row_filter,
)

__all__ = [
    "run_analysis",
    "run_multi_analysis",
]


def _column_meaning_query(prompt: str, *, multi: bool = False) -> str:
    """컬럼 의미 추정용 LLM 프롬프트. 집계·필터 단축 없이 설명만 요청한다."""
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


def run_analysis(
    df: pd.DataFrame,
    prompt: str,
    *,
    base_url: str,
    model: str,
    use_budget_profile: bool = False,
    skip_aggregate_shortcuts: bool = False,
) -> tuple[object, str, dict]:
    """DataFrame과 사용자 요청을 PandasAI에 전달해 결과를 반환한다."""
    if not prompt.strip():
        raise ValueError("분석 요청을 입력해 주세요.")

    # 컬럼 의미 설명은 규칙 단축 없이 LLM으로 보낸다.
    if is_column_meaning_request(prompt):
        return chat(
            df,
            _column_meaning_query(prompt),
            base_url=base_url,
            model=model,
            output_type=None,
        )

    output_type = resolve_output_type(prompt)

    # 차트는 LLM matplotlib(한글 깨짐) 대신 자체 렌더러를 우선 사용한다.
    if output_type == "plot":
        chart_path = generate_fallback_chart(df, prompt)
        if chart_path:
            return None, "차트 결과를 생성했습니다.", {"chart_path": chart_path}

    # 파일 요약은 LLM 없이 규칙 기반으로 작성한다.
    from core.file_summary import build_file_summary, is_summary_request

    if is_summary_request(prompt):
        summary = build_file_summary(df, use_budget_profile=use_budget_profile)
        return None, summary, {}

    # '비용명별 실행예산 합계' 등 그룹 집계 단축 경로 (질의 해석형 — 축소 후보).
    if output_type != "plot" and not skip_aggregate_shortcuts:
        grouped = build_groupby_aggregate_table(
            df,
            prompt,
            use_budget_profile=use_budget_profile,
        )
        if grouped is not None:
            table, summary = grouped
            return table, summary, {"aggregation": {"operation": "groupby"}}

    # '집행계가 0인데 실행예산이 있는' 같은 조건 필터는 값 일치보다 먼저.
    if output_type == "dataframe":
        conditioned = try_condition_row_filter(df, prompt)
        if conditioned is not None:
            return (
                conditioned,
                f"조건 필터 결과: {len(conditioned):,}행",
                {},
            )

    # 값 필터를 리스트 시드보다 먼저 적용한다 (예: 비용명 121만).
    if output_type == "dataframe" and not is_complex_analysis(prompt):
        direct = _filter_by_mentioned_value(df, prompt)
        if direct is not None and not direct.empty:
            return (
                direct,
                f"데이터 값 일치 결과: {len(direct):,}행",
                {},
            )

    # 값 제약이 없을 때만 컬럼 전체 리스트 시드 사용
    if output_type == "dataframe" and is_list_request(prompt):
        seed = _build_list_seed_frame(df, prompt)
        if seed is not None and not seed.empty:
            return seed, f"리스트 결과: {len(seed):,}행", {}

    # LLM 분석 계획 → 범용 실행기 → 검증 → (선택) 해석
    # 비교/집행률/해석 등은 표 키워드가 없어도 구조화 분석 후보로 본다.
    if output_type == "dataframe" or wants_structured_analysis(prompt):
        planned = try_analysis_pipeline(
            prompt,
            df,
            base_url=base_url,
            model=model,
            use_budget_profile=use_budget_profile,
        )
        if planned is not None:
            return planned.dataframe, planned.reply, dict(planned.meta)

    query = (
        "사용자의 요청을 현재 DataFrame의 실제 컬럼명과 데이터 타입에 맞춰 "
        "pandas 연산으로 수행하세요.\n"
        "필터링, 정렬, 집계, 그룹화, 피벗, 통계 등 요청받은 종류를 스스로 판단하세요.\n"
        "특정 컬럼이나 데이터 형식을 가정하지 마세요.\n"
        "스키마 힌트는 참고용이며, 컬럼을 임의로 rewrite하지 마세요.\n"
        "반복된 상위 분류 값은 분석용 복사본에서만 채운 값일 수 있으므로 "
        "같은 분류의 모든 행을 필터링할 때 사용하세요.\n"
        "'리스트', '목록', '표', '보여줘' 요청은 반드시 DataFrame으로 반환하세요.\n"
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
    except RuntimeError:
        if output_type == "plot":
            chart_path = generate_fallback_chart(df, prompt)
            if chart_path:
                return None, "차트 결과를 생성했습니다.", {"chart_path": chart_path}
        if output_type == "dataframe":
            fallback = _filter_by_mentioned_value(df, prompt)
            if fallback is not None and not fallback.empty:
                return (
                    fallback,
                    f"데이터 값 일치 결과: {len(fallback):,}행",
                    {},
                )
        raise

    # PandasAI가 예외 없이 빈 표를 주면 값 일치 폴백으로 한 번 더 시도한다.
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
        # 결과가 요약 표면 그걸로, 아니면 원본 df로 차트 생성
        chart_source = result if isinstance(result, pd.DataFrame) and not result.empty else df
        chart_path = generate_fallback_chart(chart_source, prompt)
        if chart_path:
            meta = {**meta, "chart_path": chart_path}
            summary = "차트 결과를 생성했습니다."

    return result, summary, meta


def run_multi_analysis(
    named_dfs: list[tuple[str, pd.DataFrame]],
    prompt: str,
    *,
    base_url: str,
    model: str,
    use_budget_profile: bool = False,
    skip_metric_aggregate: bool = False,
) -> tuple[object, str, dict]:
    """여러 DataFrame을 SmartDatalake로 동시에 분석한다."""
    if len(named_dfs) < 2:
        raise ValueError("동시 분석에는 파일 2개 이상이 필요합니다.")
    if not prompt.strip():
        raise ValueError("분석 요청을 입력해 주세요.")

    if is_column_meaning_request(prompt):
        return chat_multi(
            named_dfs,
            _column_meaning_query(prompt, multi=True),
            base_url=base_url,
            model=model,
            output_type=None,
        )

    output_type = resolve_output_type(prompt)

    # 차트는 자체 렌더러를 우선 사용한다 (한글·값 라벨·축 포맷 보장).
    if output_type == "plot":
        chart_path = generate_multi_file_chart(named_dfs, prompt)
        if chart_path:
            return None, "차트 결과를 생성했습니다.", {"chart_path": chart_path}

    from core.file_summary import build_multi_file_summary, is_summary_request

    if is_summary_request(prompt):
        summary = build_multi_file_summary(
            named_dfs,
            use_budget_profile=use_budget_profile,
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
            part = try_condition_row_filter(frame, prompt)
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

    # 값 필터를 리스트 시드보다 먼저 적용한다.
    if output_type == "dataframe" and not is_complex_analysis(prompt):
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
                {},
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

    file_names = ", ".join(name for name, _ in named_dfs)
    query = (
        "여러 엑셀 파일의 DataFrame이 동시에 제공됩니다. "
        "각 dfs[i]는 서로 다른 파일이며, 파일명과 테이블명을 참고하세요.\n"
        "비교, 병합(merge/join), 교차 집계, 공통 컬럼 탐색 등 요청을 판단해 pandas로 수행하세요.\n"
        "특정 컬럼이나 데이터 형식을 가정하지 마세요.\n"
        "반복된 상위 분류 값은 원본의 빈 상세 행을 분석용으로 채운 값이므로 "
        "같은 분류의 모든 행을 필터링할 때 사용하세요.\n"
        "'리스트', '목록', '표', '보여줘' 요청은 반드시 DataFrame으로 반환하세요.\n"
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
        elif output_type == "dataframe":
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
