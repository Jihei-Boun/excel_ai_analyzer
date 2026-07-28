"""자연어 요청을 PandasAI로 실행하는 범용 분석 진입점."""

from __future__ import annotations

import re

import pandas as pd

from core.chart_utils import generate_fallback_chart, generate_multi_file_chart
from core.constants import (
    AMOUNT_COLUMN_HINTS,
    BUDGET_FOOTER_LABELS,
    CODE_METRIC_ABS_MAX,
    CODE_METRIC_INT_RATIO,
    CODE_METRIC_NAME_HINTS,
    CODE_METRIC_SAMPLE_SIZE,
    SUMMARY_RANKING_BITS,
)
from core.excel_loader import find_merged_header_pair, merged_header_base
from core.pandasai_config import chat, chat_multi, prepare_dataframe_for_ai

_COMPLEX_KEYWORDS = (
    "상위",
    "하위",
    "합계",
    "평균",
    "최대",
    "최소",
    "정렬",
    "그룹",
    "피벗",
    "통계",
    "비율",
    "비교",
    "상관",
    "추이",
    "차트",
    "그래프",
    "sum",
    "mean",
    "avg",
    "max",
    "min",
    "sort",
    "group",
    "pivot",
    "chart",
    "plot",
)

_LIST_REQUEST_KEYWORDS = (
    "리스트",
    "목록",
    "나열",
    "뽑아",
    "list",
)


def run_analysis(
    df: pd.DataFrame,
    prompt: str,
    *,
    base_url: str,
    model: str,
    use_budget_profile: bool = False,
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

    # '비용명별 실행예산 합계' 등 그룹 집계는 LLM 전에 처리한다.
    if output_type != "plot":
        grouped = build_groupby_aggregate_table(
            df,
            prompt,
            use_budget_profile=use_budget_profile,
        )
        if grouped is not None:
            table, summary = grouped
            return table, summary, {}

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
        "반복된 상위 분류 값은 원본의 빈 상세 행을 분석용으로 채운 값이므로 "
        "같은 분류의 모든 행을 필터링할 때 사용하세요.\n"
        "'리스트', '목록', '표', '보여줘' 요청은 반드시 DataFrame으로 반환하세요.\n"
        "차트·그래프·시각화 요청은 plot type으로 차트를 저장하고 경로를 반환하세요.\n"
        "합계·총합·평균 등 집계 요청도 가능하면 "
        "행 라벨(분류명)과 컬럼명·집계값으로 된 작은 DataFrame으로 반환하세요.\n"
        "예: 열이 ['', '계획예산']이고 행이 ['연구활동비', 12345] 형태.\n"
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
    if output_type == "dataframe" and metric_aggregate:
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


def _filter_multi_by_mentioned_value(
    named_dfs: list[tuple[str, pd.DataFrame]],
    prompt: str,
) -> pd.DataFrame | None:
    """각 파일에서 값 일치 행을 찾아 출처 컬럼과 함께 합친다."""
    parts: list[pd.DataFrame] = []
    for name, df in named_dfs:
        filtered = _filter_by_mentioned_value(df, prompt)
        if filtered is None or filtered.empty:
            continue
        part = filtered.copy()
        part.insert(0, "출처파일", name)
        parts.append(part)
    if not parts:
        return None
    return pd.concat(parts, ignore_index=True)


_CHART_KEYWORDS = (
    "차트",
    "그래프",
    "막대그래프",
    "원그래프",
    "시각화",
    "chart",
    "plot",
    "graph",
    "bar chart",
)


def _expects_plot(prompt: str) -> bool:
    """차트·그래프 시각화 요청인지 판별한다."""
    lowered = prompt.lower()
    return any(keyword in lowered for keyword in _CHART_KEYWORDS)


def _resolve_output_type(prompt: str) -> str | None:
    """요청에 맞는 PandasAI output_type을 고른다. 차트는 plot을 우선한다."""
    if _expects_plot(prompt):
        return "plot"
    if _expects_dataframe(prompt):
        return "dataframe"
    return None


def _expects_dataframe(prompt: str) -> bool:
    """표 형태 결과를 요구하는 표현인지 판별한다."""
    lowered = prompt.lower()
    table_keywords = (
        "리스트",
        "목록",
        "표",
        "보여",
        "출력",
        "조회",
        "검색",
        "필터",
        "추출",
        "행",
        "열",
        "상위",
        "하위",
        "정렬",
        "list",
        "table",
        "show",
        "filter",
        "rows",
        "columns",
    )
    return any(keyword in lowered for keyword in table_keywords)


def _is_list_request(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(keyword in lowered for keyword in _LIST_REQUEST_KEYWORDS)


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


def _is_complex_analysis(prompt: str) -> bool:
    """집계·순위·시각화처럼 단순 값 필터로 해결할 수 없는 요청인지 판별한다."""
    if detect_aggregate_op(prompt) is not None:
        return True
    lowered = prompt.lower()
    return any(keyword in lowered for keyword in _COMPLEX_KEYWORDS)


def _filter_by_mentioned_value(
    df: pd.DataFrame,
    prompt: str,
) -> pd.DataFrame | None:
    """요청에 명시된 실제 셀 값으로 행을 찾는 범용 폴백.

    문자·숫자 컬럼 모두 검색한다. 숫자 코드(121 등)는 앞뒤가 숫자가 아닐 때만 매칭한다.
    셀 값이 프롬프트에 그대로 없어도, '인건비' → '내부인건비'처럼
    프롬프트 핵심어가 셀 값에 포함되면 매칭한다.
    """
    prepared = prepare_dataframe_for_ai(df)
    normalized_prompt = _normalize_text(prompt)
    prompt_tokens = _filter_tokens_from_prompt(prompt)
    preferred_columns = _mentioned_columns(prepared, normalized_prompt)
    matches: list[tuple[int, int, str, object]] = []

    search_columns = preferred_columns or list(prepared.columns)
    _collect_value_matches(
        prepared,
        search_columns,
        normalized_prompt,
        prompt_tokens,
        matches,
    )

    if not matches and preferred_columns:
        other_columns = [c for c in prepared.columns if c not in preferred_columns]
        _collect_value_matches(
            prepared,
            other_columns,
            normalized_prompt,
            prompt_tokens,
            matches,
        )

    if not matches:
        return None

    # priority 높은 것 우선, 그다음 매칭 키 길이
    best_priority = max(priority for priority, _, _, _ in matches)
    candidates = [m for m in matches if m[0] == best_priority]
    longest = max(length for _, length, _, _ in candidates)

    mask = pd.Series(False, index=prepared.index)
    for priority, length, column, value in candidates:
        if priority != best_priority or length != longest:
            continue
        mask |= _column_equals(prepared[column], value)

    result = prepared.loc[mask]
    return result.reset_index(drop=True) if not result.empty else None


def resolve_filter_source(
    full_df: pd.DataFrame,
    filtered_df: pd.DataFrame | None,
    prompt: str,
    *,
    keep_filter_for_aggregate: bool = True,
) -> tuple[pd.DataFrame, bool]:
    """필터된 데이터에 요청 값이 없으면 원본으로 되돌려 (source, reset)을 반환한다.

    reset=True이면 호출 측에서 필터 세션 상태를 비워야 한다.
    """
    if full_df is None or full_df.empty:
        return full_df, False

    if keep_filter_for_aggregate and detect_aggregate_op(prompt) is not None:
        if filtered_df is not None and len(filtered_df) > 0:
            return filtered_df, False
        return full_df, False

    if _expects_plot(prompt):
        if filtered_df is not None and len(filtered_df) > 0:
            return filtered_df, False
        return full_df, False

    if filtered_df is None or len(filtered_df) == 0:
        return full_df, False

    on_filtered = _filter_by_mentioned_value(filtered_df, prompt)
    on_full = _filter_by_mentioned_value(full_df, prompt)
    if (on_filtered is None or on_filtered.empty) and (
        on_full is not None and not on_full.empty
    ):
        return full_df, True

    return filtered_df, False


def _collect_value_matches(
    df: pd.DataFrame,
    columns: list,
    normalized_prompt: str,
    prompt_tokens: list[str],
    matches: list[tuple[int, int, str, object]],
) -> None:
    for column in columns:
        series = df[column]
        for value in series.dropna().unique():
            text = _cell_match_text(value)
            if not text:
                continue
            normalized = _normalize_text(text)
            if _is_aggregate_label_false_positive(normalized, normalized_prompt, df):
                continue
            scored = _score_value_prompt_match(
                normalized,
                normalized_prompt,
                prompt_tokens,
            )
            if scored is None:
                continue
            priority, key_length = scored
            matches.append((priority, key_length, str(column), value))


def _score_value_prompt_match(
    normalized_value: str,
    normalized_prompt: str,
    prompt_tokens: list[str],
) -> tuple[int, int] | None:
    """(priority, key_length). priority: 2=완전일치, 1=값이 프롬프트에 포함, 0=토큰이 값에 포함."""
    if not normalized_value:
        return None

    if normalized_value.isdigit():
        if not re.search(
            rf"(?<!\d){re.escape(normalized_value)}(?!\d)",
            normalized_prompt,
        ):
            return None
        return (2, len(normalized_value))

    if len(normalized_value) >= 2 and normalized_value in normalized_prompt:
        priority = 2 if _is_exact_value_mention(normalized_value, normalized_prompt) else 1
        return (priority, len(normalized_value))

    # '인건비' → '내부인건비' 처럼 프롬프트 핵심어가 셀 값에 포함된 경우
    best_token_len = 0
    for token in prompt_tokens:
        if len(token) < 2 or len(token) > len(normalized_value):
            continue
        if token == normalized_value:
            return (2, len(token))
        if token in normalized_value:
            best_token_len = max(best_token_len, len(token))
    if best_token_len >= 2:
        return (0, best_token_len)
    return None


def _filter_tokens_from_prompt(prompt: str) -> list[str]:
    """필터 검색용 핵심 토큰을 프롬프트에서 뽑는다."""
    text = prompt.strip()
    text = re.split(r"[,\n.?!]", text)[0]
    for noise in sorted(_PROMPT_NOISE, key=len, reverse=True):
        text = re.sub(re.escape(noise), " ", text, flags=re.IGNORECASE)
    tokens = re.findall(r"[0-9A-Za-z가-힣]+", text)
    normalized_tokens = [_normalize_text(token) for token in tokens]
    return [
        token
        for token in normalized_tokens
        if len(token) >= 2 and token not in {_normalize_text(n) for n in _PROMPT_NOISE}
    ]

def _cell_match_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return str(value).strip()
    try:
        import numpy as np

        if isinstance(value, (np.integer, np.floating)):
            number = float(value)
            if number.is_integer():
                return str(int(number))
            return str(value).strip()
    except ImportError:
        pass
    return str(value).strip()


def _value_mentioned_in_prompt(normalized_value: str, normalized_prompt: str) -> bool:
    if not normalized_value:
        return False
    if normalized_value.isdigit():
        return bool(
            re.search(
                rf"(?<!\d){re.escape(normalized_value)}(?!\d)",
                normalized_prompt,
            )
        )
    return len(normalized_value) >= 2 and normalized_value in normalized_prompt


def _prompt_requests_total_rows(normalized_prompt: str) -> bool:
    """'합계 행만'처럼 집계 라벨 행 자체를 요청하는지."""
    markers = (
        "합계행",
        "합계만",
        "합계줄",
        "소계행",
        "소계만",
        "총계행",
        "합계인",
        "합계가",
        "합계를",
        "소계를",
        "합계표",
        "소계표",
    )
    return any(marker in normalized_prompt for marker in markers)


def _is_aggregate_label_false_positive(
    normalized_value: str,
    normalized_prompt: str,
    df: pd.DataFrame,
) -> bool:
    """컬럼명(실행예산_합계)에만 있는 '합계'를 셀 값 매칭에서 제외한다."""
    from core.pandasai_config import is_total_label

    if _prompt_requests_total_rows(normalized_prompt):
        return False

    compact = normalized_value.replace(" ", "")
    if not is_total_label(compact):
        return False

    for column in df.columns:
        col_norm = _normalize_text(str(column))
        if compact not in col_norm or col_norm not in normalized_prompt:
            continue
        remainder = normalized_prompt.replace(col_norm, "", 1)
        if compact not in remainder:
            return True
    return False


def is_metric_aggregate_request(
    prompt: str,
    df: pd.DataFrame | None = None,
    *,
    named_dfs: list[tuple[str, pd.DataFrame]] | None = None,
) -> bool:
    """수치 컬럼에 대한 합계·평균 등 집계 요청인지 (차트 요청 포함)."""
    if _match_aggregate_op(prompt) is None:
        return False
    probe = df
    if probe is None and named_dfs:
        probe = next((frame for _, frame in named_dfs if frame is not None and not frame.empty), None)
    if probe is None or probe.empty:
        return False
    return find_mentioned_numeric_column(probe, prompt) is not None


def _is_exact_value_mention(normalized_value: str, normalized_prompt: str) -> bool:
    """'비용명이 121인'처럼 값이 독립 토큰으로 쓰였는지."""
    if normalized_value.isdigit():
        return bool(
            re.search(
                rf"(?<!\d){re.escape(normalized_value)}(?!\d)",
                normalized_prompt,
            )
        )
    return normalized_value in normalized_prompt


def _column_equals(series: pd.Series, value: object) -> pd.Series:
    target = _cell_match_text(value)
    return series.map(_cell_match_text) == target


def _mentioned_columns(df: pd.DataFrame, normalized_prompt: str) -> list[str]:
    """프롬프트에 이름만 등장하는 컬럼을 긴 이름 우선으로 고른다."""
    scored: list[tuple[int, str]] = []
    for column in df.columns:
        normalized = _normalize_text(str(column))
        if len(normalized) >= 2 and normalized in normalized_prompt:
            scored.append((len(normalized), column))
    scored.sort(reverse=True)
    if not scored:
        return []
    best = scored[0][0]
    return [column for length, column in scored if length == best]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


_AGGREGATE_OPS = (
    ("평균", "mean"),
    ("mean", "mean"),
    ("avg", "mean"),
    ("최댓값", "max"),
    ("최대", "max"),
    ("max", "max"),
    ("최솟값", "min"),
    ("최소", "min"),
    ("min", "min"),
    ("총합", "sum"),
    ("총 합", "sum"),
    ("종합", "sum"),
    ("합계", "sum"),
    ("합산", "sum"),
    ("sum", "sum"),
    ("total", "sum"),
)


def _match_aggregate_op(prompt: str) -> str | None:
    """프롬프트에서 집계 연산 종류를 찾는다 (차트 요청 포함)."""
    lowered = prompt.lower()
    normalized = _normalize_text(prompt)
    # '실행예산의 합', '집행계 합'처럼 '합' 단독 표현도 합계로 인식한다.
    # (종합/통합처럼 단어 내부의 '합'은 제외)
    if re.search(r"(?:^|[\s(])합(?:계|산)?(?:을|를|은|는|이|가)?(?:$|[\s)])", prompt):
        return "sum"
    if re.search(r"의\s*합(?:계|산)?(?:을|를|은|는|이|가)?", prompt):
        return "sum"
    if re.search(r"별\s*합(?:계|산)?(?:을|를|은|는|이|가)?", prompt):
        return "sum"
    if re.search(r"(?:^|[^가-힣a-z0-9])합(?:계|산)?(?:을|를|은|는|이|가)$", normalized):
        return "sum"

    for keyword, op in sorted(_AGGREGATE_OPS, key=lambda item: len(item[0]), reverse=True):
        key_l = keyword.lower()
        key_n = _normalize_text(keyword)
        if key_l in lowered or key_n in normalized:
            return op
    return None


def detect_aggregate_op(prompt: str) -> str | None:
    """프롬프트에서 집계 연산 종류를 찾는다. 없으면 None.

    차트 요청은 집계 단축 경로가 아닌 시각화 경로로 보내기 위해 None을 반환한다.
    """
    if _expects_plot(prompt):
        return None
    return _match_aggregate_op(prompt)


def extract_matched_value(df: pd.DataFrame, prompt: str) -> str | None:
    """요청 문구에 등장하는 실제 셀 값(가장 긴 일치)을 반환한다."""
    detail = extract_matched_detail(df, prompt)
    return detail[1] if detail else None


def extract_matched_detail(df: pd.DataFrame, prompt: str) -> tuple[str, str] | None:
    """요청 문구에 등장하는 실제 셀 값과 컬럼명을 (컬럼, 값)으로 반환한다."""
    prepared = prepare_dataframe_for_ai(df)
    normalized_prompt = _normalize_text(prompt)
    prompt_tokens = _filter_tokens_from_prompt(prompt)
    preferred_columns = _mentioned_columns(prepared, normalized_prompt)
    matches: list[tuple[int, int, str, object]] = []

    search_columns = preferred_columns or list(prepared.columns)
    _collect_value_matches(
        prepared,
        search_columns,
        normalized_prompt,
        prompt_tokens,
        matches,
    )
    if not matches and preferred_columns:
        other_columns = [c for c in prepared.columns if c not in preferred_columns]
        _collect_value_matches(
            prepared,
            other_columns,
            normalized_prompt,
            prompt_tokens,
            matches,
        )

    if not matches:
        return None

    best_priority = max(priority for priority, _, _, _ in matches)
    candidates = [m for m in matches if m[0] == best_priority]
    longest = max(length for _, length, _, _ in candidates)
    for priority, length, column, value in candidates:
        if priority == best_priority and length == longest:
            return column, _cell_match_text(value)
    return None


def build_filter_summary(
    prompt: str,
    result_df: pd.DataFrame,
    full_df: pd.DataFrame | None = None,
    *,
    file_count: int | None = None,
) -> str | None:
    """필터 결과를 채팅용 한 줄 요약으로 만든다.

    예: ``연구활동비 · 42행 · 예산과목 일치``
    """
    if result_df is None or result_df.empty:
        return None

    parts: list[str] = []
    label = infer_context_label(
        prompt=prompt,
        result_df=result_df,
        full_df=full_df,
    )
    if label:
        parts.append(str(label))

    parts.append(f"{len(result_df):,}행")

    search_df = full_df if full_df is not None and not full_df.empty else result_df
    detail = extract_matched_detail(search_df, prompt) if prompt else None
    if detail:
        column, value = detail
        if label and _normalize_text(value) == _normalize_text(str(label)):
            parts.append(f"{column} 일치")
        else:
            parts.append(f"{column}={value}")

    resolved_files = file_count
    if resolved_files is None and "출처파일" in result_df.columns:
        resolved_files = int(result_df["출처파일"].nunique())
    if resolved_files and resolved_files > 1:
        parts.append(f"{resolved_files}개 파일")

    return " · ".join(parts) if parts else None


def find_mentioned_column(df: pd.DataFrame, prompt: str) -> str | None:
    """프롬프트에 언급된 컬럼명을 긴 이름 우선으로 고른다."""
    mentioned = _mentioned_columns(df, _normalize_text(prompt))
    return mentioned[0] if mentioned else None


def find_mentioned_numeric_column(df: pd.DataFrame, prompt: str) -> str | None:
    """프롬프트에 언급된 수치형 컬럼을 하나 찾는다."""
    columns = find_mentioned_numeric_columns(df, prompt)
    return columns[0] if columns else None


def find_mentioned_numeric_columns(df: pd.DataFrame, prompt: str) -> list[str]:
    """프롬프트에 언급된 수치형 컬럼을 모두 찾는다 (금액 컬럼·합계열 우선)."""
    normalized_prompt = _normalize_text(prompt)
    group_col = find_groupby_column(df, prompt)
    group_norms = set()
    if group_col:
        group_norms.add(_normalize_text(str(group_col)))
        group_norms.add(_normalize_text(merged_header_base(str(group_col))))

    scored: list[tuple[int, int, str]] = []
    for column in df.columns:
        coerced = pd.to_numeric(df[column], errors="coerce")
        if not coerced.notna().any() and not pd.api.types.is_numeric_dtype(df[column]):
            continue
        col_name = str(column)
        col_norm = _normalize_text(col_name)
        if col_norm in group_norms or _normalize_text(merged_header_base(col_name)) in group_norms:
            continue

        match_len = _column_prompt_match_length(col_name, normalized_prompt)
        if match_len <= 0:
            continue

        amount_bonus = 100 if _is_amount_metric_column(col_name) else 0
        total_bonus = 0
        wants_total = any(k in normalized_prompt for k in ("합계", "합을", "합산", "의합", "총합"))
        if wants_total and (
            col_norm.endswith("합계") or col_norm.endswith("_합계")
        ):
            total_bonus = 50
        code_penalty = -120 if _looks_like_code_metric_column(df, column) else 0
        scored.append((amount_bonus + total_bonus + code_penalty, match_len, column))

    if not scored:
        return []

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_score = scored[0][0]
    top = [item for item in scored if item[0] == best_score]
    top.sort(key=lambda item: item[1], reverse=True)

    selected: list[str] = []
    for _score, _match_len, column in top:
        base = _normalize_text(merged_header_base(str(column)))
        if any(base == _normalize_text(merged_header_base(prev)) for prev in selected):
            continue
        selected.append(column)
    return selected


def find_groupby_column(df: pd.DataFrame, prompt: str) -> str | None:
    """'비용명별', '비목분류 별로'처럼 그룹 기준 컬럼을 찾는다."""
    if df is None or df.empty or not prompt:
        return None

    match = re.search(r"([0-9A-Za-z가-힣]+)\s*별(?:로)?", prompt)
    if not match:
        return None

    key = match.group(1)
    key_norm = _normalize_text(key)
    if len(key_norm) < 2:
        return None

    # 병합 헤더 쌍이면 명칭(오른쪽)을 그룹 키로 쓴다 (비용명 코드 대신 비용명_2)
    pair = find_merged_header_pair(df.columns, key)
    if pair:
        return pair[1]

    exact: list[str] = []
    partial: list[tuple[int, str]] = []
    for column in df.columns:
        name = str(column)
        norm = _normalize_text(name)
        base = _normalize_text(merged_header_base(name))
        if norm == key_norm or base == key_norm:
            exact.append(name)
        elif key_norm in norm or key_norm in base:
            partial.append((len(norm), name))

    if exact:
        # 문자 라벨 컬럼을 코드형 숫자 컬럼보다 선호
        exact.sort(
            key=lambda col: (
                0 if pd.api.types.is_numeric_dtype(df[col]) else 1,
                len(str(col)),
            ),
            reverse=True,
        )
        return exact[0]
    if partial:
        partial.sort(reverse=True)
        return partial[0][1]
    return None


def build_groupby_aggregate_table(
    df: pd.DataFrame,
    prompt: str,
    *,
    use_budget_profile: bool = False,
) -> tuple[pd.DataFrame, str] | None:
    """'비용명별 집행계 합계'처럼 그룹별 집계 표를 만든다.

    '비목분류별 계획예산'처럼 합계 단어가 없어도 X별 Y 요청이면 합산으로 처리한다.
    use_budget_profile=True이면 내부흡수액·외부유출액 등 예산 footer 행을 제외한다.
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
        if col != group_col and not _looks_like_code_metric_column(df, col)
    ]
    if not metric_cols:
        return None

    from core.pandasai_config import (
        exclude_total_rows,
        is_total_label,
        prepare_dataframe_for_ai,
        sum_metric_excluding_totals,
    )

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

    # 파일에 처음 등장하는 순서 유지 (가나다·금액 정렬 금지)
    ordered_labels: list[str] = []
    seen_labels: set[str] = set()
    for label in work["_group_label"].tolist():
        text = str(label)
        if not text or text in seen_labels or is_total_label(text):
            continue
        if use_budget_profile and _is_budget_footer_label(text):
            continue
        seen_labels.add(text)
        ordered_labels.append(text)

    for label in ordered_labels:
        group = work.loc[work["_group_label"] == label]
        row: dict[str, object] = {str(group_col): label}
        for metric_col in metric_cols:
            resolved = _resolve_metric_column(group, metric_col) or metric_col
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


def _is_explicit_groupby_prompt(prompt: str) -> bool:
    normalized = _normalize_text(prompt)
    return (
        "별" in prompt
        or "그룹" in prompt
        or "groupby" in normalized
        or "groupby" in prompt.lower()
    )


def _is_budget_footer_label(value: object) -> bool:
    """내부흡수액·외부유출액처럼 비목 집계에서 제외할 하단 요약 라벨인지."""
    text = _cell_match_text(value)
    if not text:
        return False
    compact = _normalize_text(text)
    return compact in {_normalize_text(label) for label in BUDGET_FOOTER_LABELS}


def _column_prompt_match_length(column: str, normalized_prompt: str) -> int:
    """컬럼명·세그먼트가 프롬프트에 있으면 매칭 길이를 반환한다."""
    generic_parts = {"합계", "합", "계", "평균", "total", "sum", "avg", "mean"}
    col_norm = _normalize_text(column)
    if len(col_norm) >= 2 and col_norm in normalized_prompt:
        return len(col_norm)

    parts = [p for p in re.split(r"[_\s]+", str(column)) if p]
    best = 0
    for part in parts:
        part_norm = _normalize_text(part)
        if len(part_norm) < 2 or part_norm in generic_parts:
            continue
        if part_norm in normalized_prompt:
            best = max(best, len(part_norm))

    base = _normalize_text(merged_header_base(str(column)))
    if len(base) >= 2 and base not in generic_parts and base in normalized_prompt:
        best = max(best, len(base))
    return best


def _is_amount_metric_column(name: str) -> bool:
    normalized = _normalize_text(str(name))
    return any(hint in normalized for hint in AMOUNT_COLUMN_HINTS)


def _looks_like_code_metric_column(df: pd.DataFrame, column: object) -> bool:
    """비용명(121, 201)처럼 코드성 수치 컬럼인지 판별한다."""
    name = str(column)
    if not pd.api.types.is_numeric_dtype(df[column]):
        return False
    norm = _normalize_text(name)
    base = _normalize_text(merged_header_base(name))
    name_looks_code = any(
        hint in norm or hint == base for hint in CODE_METRIC_NAME_HINTS
    )
    if not name_looks_code:
        return False
    sample = (
        pd.to_numeric(df[column], errors="coerce")
        .dropna()
        .head(CODE_METRIC_SAMPLE_SIZE)
    )
    if sample.empty:
        return True
    ints = sample.map(
        lambda v: float(v).is_integer() and abs(float(v)) < CODE_METRIC_ABS_MAX
    )
    return bool(ints.mean() > CODE_METRIC_INT_RATIO)


def format_context_label(label: str | None) -> str:
    """표시용 행 라벨. '연구활동비항목' → '연구활동비'처럼 꼬리표 정리."""
    if not label:
        return "합계"
    text = str(label).strip()
    text = re.sub(r"(항목|목록|리스트|내역)$", "", text).strip()
    return text or str(label).strip()


_PROMPT_NOISE = (
    "을",
    "를",
    "이",
    "가",
    "은",
    "는",
    "의",
    "에",
    "로",
    "으로",
    "만",
    "좀",
    "좀만",
    "리스트",
    "목록",
    "표",
    "보여줘",
    "보여",
    "주세요",
    "해줘",
    "해봐",
    "출력",
    "조회",
    "검색",
    "필터",
    "추출",
    "항목",
    "내역",
    "행",
    "열",
    "데이터",
    "전체",
    "모든",
    "알려줘",
    "구해줘",
    "계산",
    "총합",
    "총 합",
    "합계",
    "합을",
    "평균",
    "최댓값",
    "최솟값",
    "최대",
    "최소",
)


def infer_context_label(
    *,
    prompt: str | None = None,
    result_df: pd.DataFrame | None = None,
    full_df: pd.DataFrame | None = None,
) -> str | None:
    """필터/목록 요청의 행 라벨을 여러 단서로 추론한다.

    우선순위:
    1) 결과 표에서 값이 하나뿐인 문자 컬럼
    2) full_df/result_df 셀 값과 프롬프트 매칭
    3) 프롬프트에서 잡음 단어를 제거한 핵심 구
    """
    # 1) 필터 결과에서 단일 분류값 (가장 긴 라벨 우선)
    if result_df is not None and not result_df.empty:
        candidates: list[tuple[int, str]] = []
        for column in result_df.columns:
            series = result_df[column]
            if pd.api.types.is_numeric_dtype(series):
                continue
            values = [
                str(v).strip()
                for v in series.dropna().unique()
                if str(v).strip() and str(v).strip().lower() not in {"nan", "none"}
            ]
            if len(values) == 1 and len(values[0]) >= 2:
                candidates.append((len(values[0]), values[0]))
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1]

    # 2) 셀 값 매칭
    for frame in (full_df, result_df):
        if frame is None or frame.empty or not prompt:
            continue
        matched = extract_matched_value(frame, prompt)
        if matched:
            return matched

    # 3) 프롬프트에서 핵심 명사구 추출
    if prompt:
        from_prompt = _label_from_prompt_text(prompt)
        if from_prompt:
            return from_prompt
    return None


def _label_from_prompt_text(prompt: str) -> str | None:
    """'연구활동비항목을 리스트로 보여줘' → '연구활동비항목'."""
    text = prompt.strip()
    # 조사/요청어 앞까지를 후보로
    text = re.split(r"[,\n.?!]", text)[0]
    for noise in sorted(_PROMPT_NOISE, key=len, reverse=True):
        text = re.sub(re.escape(noise), " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", "", text)
    # 남아있는 한글/영문/숫자만
    text = re.sub(r"[^0-9A-Za-z가-힣]", "", text)
    if len(text) < 2:
        return None
    # 너무 긴 문장은 제외
    if len(text) > 40:
        return None
    return text


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
        if not _looks_like_code_metric_column(df, col)
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
        col = _resolve_metric_column(df, metric_col)
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
) -> tuple[pd.DataFrame, str] | None:
    """다중 파일 집계를 파일별 행 · 수치 컬럼 열 요약 표로 만든다.

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
            col = _resolve_metric_column(df, metric_col)
            if col is None:
                continue
            from core.pandasai_config import sum_metric_excluding_totals

            value = sum_metric_excluding_totals(df, col)
            if value is None:
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

    prefix = f"{ctx} · 파일별" if ctx and ctx != "합계" else "파일별"
    summary = f"{prefix} {op_name} — " + " | ".join(summary_parts)
    return table, summary


def _aggregate_reducer(op: str) -> tuple[str, object]:
    if op == "mean":
        return "평균", lambda s: float(s.mean())
    if op == "max":
        return "최댓값", lambda s: float(s.max())
    if op == "min":
        return "최솟값", lambda s: float(s.min())
    return "총합", lambda s: float(s.sum())


def _resolve_metric_column(df: pd.DataFrame, wanted: str) -> str | None:
    """파일마다 컬럼명이 달라도 같은 수치 열을 찾는다."""
    if wanted in df.columns:
        return wanted
    target = _normalize_text(str(wanted))
    for column in df.columns:
        if _normalize_text(str(column)) == target:
            return column
    for column in df.columns:
        norm = _normalize_text(str(column))
        if target and (target in norm or norm in target):
            coerced = pd.to_numeric(df[column], errors="coerce")
            if coerced.notna().any() or pd.api.types.is_numeric_dtype(df[column]):
                return column
    return None


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

