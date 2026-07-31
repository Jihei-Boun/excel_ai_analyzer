"""자연어 요청을 PandasAI로 실행하는 범용 분석 진입점."""

from __future__ import annotations

import pandas as pd

from core.aggregates import (
    _aggregate_reducer,
    _build_list_seed_frame,
    _is_budget_footer_label,
    build_context_aggregate_table,
    build_groupby_aggregate_table,
    build_multi_context_aggregate_table,
    scalar_to_context_table,
    split_frames_by_source,
)
from core.chart_utils import generate_fallback_chart, generate_multi_file_chart
from core.column_match import (
    _column_prompt_match_length,
    _is_amount_metric_column,
    _is_explicit_groupby_prompt,
    _looks_like_code_metric_column,
    _mentioned_columns,
    _resolve_metric_column,
    find_groupby_column,
    find_mentioned_column,
    find_mentioned_numeric_column,
    find_mentioned_numeric_columns,
    looks_like_code_metric_column,
    resolve_metric_column,
)
from core.pandasai_config import chat, chat_multi
from core.prompt_intent import (
    _COMPLEX_KEYWORDS,
    _LIST_REQUEST_KEYWORDS,
    _expects_dataframe,
    _expects_plot,
    _is_complex_analysis,
    _is_list_request,
    _match_aggregate_op,
    _resolve_output_type,
    _wants_table_and_chart,
    detect_aggregate_op,
    wants_table_and_chart,
)
from core.text_normalize import _normalize_text, normalize_text
from core.value_filter import (
    _cell_match_text,
    _column_equals,
    _filter_by_mentioned_value,
    _filter_multi_by_mentioned_value,
    _filter_tokens_from_prompt,
    _is_aggregate_label_false_positive,
    _is_exact_value_mention,
    _label_from_prompt_text,
    _prompt_requests_total_rows,
    _score_value_prompt_match,
    _value_mentioned_in_prompt,
    build_filter_summary,
    extract_matched_detail,
    extract_matched_value,
    format_context_label,
    infer_context_label,
    is_metric_aggregate_request,
    resolve_filter_source,
)

__all__ = [
    "run_analysis",
    "run_multi_analysis",
    # prompt_intent
    "_COMPLEX_KEYWORDS",
    "_LIST_REQUEST_KEYWORDS",
    "_expects_dataframe",
    "_expects_plot",
    "_is_complex_analysis",
    "_is_list_request",
    "_match_aggregate_op",
    "_resolve_output_type",
    "detect_aggregate_op",
    "wants_table_and_chart",
    "_wants_table_and_chart",
    # text_normalize
    "_normalize_text",
    "normalize_text",
    # column_match
    "_column_prompt_match_length",
    "_is_amount_metric_column",
    "_is_explicit_groupby_prompt",
    "_looks_like_code_metric_column",
    "_mentioned_columns",
    "_resolve_metric_column",
    "find_groupby_column",
    "find_mentioned_column",
    "find_mentioned_numeric_column",
    "find_mentioned_numeric_columns",
    "looks_like_code_metric_column",
    "resolve_metric_column",
    # value_filter
    "_cell_match_text",
    "_column_equals",
    "_filter_by_mentioned_value",
    "_filter_multi_by_mentioned_value",
    "_filter_tokens_from_prompt",
    "_is_aggregate_label_false_positive",
    "_is_exact_value_mention",
    "_label_from_prompt_text",
    "_prompt_requests_total_rows",
    "_score_value_prompt_match",
    "_value_mentioned_in_prompt",
    "build_filter_summary",
    "extract_matched_detail",
    "extract_matched_value",
    "format_context_label",
    "infer_context_label",
    "is_metric_aggregate_request",
    "resolve_filter_source",
    # aggregates
    "_aggregate_reducer",
    "_build_list_seed_frame",
    "_is_budget_footer_label",
    "build_context_aggregate_table",
    "build_groupby_aggregate_table",
    "build_multi_context_aggregate_table",
    "scalar_to_context_table",
    "split_frames_by_source",
]


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

    output_type = _resolve_output_type(prompt)

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

    # 값 필터를 리스트 시드보다 먼저 적용한다 (예: 비용명 121만).
    if output_type == "dataframe" and not _is_complex_analysis(prompt):
        direct = _filter_by_mentioned_value(df, prompt)
        if direct is not None and not direct.empty:
            return (
                direct,
                f"데이터 값 일치 결과: {len(direct):,}행",
                {},
            )

    # 값 제약이 없을 때만 컬럼 전체 리스트 시드 사용
    if output_type == "dataframe" and _is_list_request(prompt):
        seed = _build_list_seed_frame(df, prompt)
        if seed is not None and not seed.empty:
            return seed, f"리스트 결과: {len(seed):,}행", {}

    query = (
        "사용자의 요청을 현재 DataFrame의 실제 컬럼명과 데이터 타입에 맞춰 "
        "pandas 연산으로 수행하세요.\n"
        "필터링, 정렬, 집계, 그룹화, 피벗, 통계 등 요청의 종류를 스스로 판단하세요.\n"
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

    output_type = _resolve_output_type(prompt)

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

    # 값 필터를 리스트 시드보다 먼저 적용한다.
    if output_type == "dataframe" and not _is_complex_analysis(prompt):
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

    if output_type == "dataframe" and _is_list_request(prompt):
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
