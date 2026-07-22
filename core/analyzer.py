"""자연어 요청을 PandasAI로 실행하는 범용 분석 진입점."""

from __future__ import annotations

import re

import pandas as pd

from core.chart_utils import generate_fallback_chart, generate_multi_file_chart
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
    lowered = prompt.lower()
    return any(keyword in lowered for keyword in _COMPLEX_KEYWORDS)


def _filter_by_mentioned_value(
    df: pd.DataFrame,
    prompt: str,
) -> pd.DataFrame | None:
    """요청에 명시된 실제 셀 값으로 행을 찾는 범용 폴백.

    문자·숫자 컬럼 모두 검색한다. 숫자 코드(121 등)는 앞뒤가 숫자가 아닐 때만 매칭한다.
    """
    prepared = prepare_dataframe_for_ai(df)
    normalized_prompt = _normalize_text(prompt)
    preferred_columns = _mentioned_columns(prepared, normalized_prompt)
    matches: list[tuple[int, int, str, object]] = []

    search_columns = preferred_columns or list(prepared.columns)
    _collect_value_matches(prepared, search_columns, normalized_prompt, matches)

    if not matches and preferred_columns:
        other_columns = [c for c in prepared.columns if c not in preferred_columns]
        _collect_value_matches(prepared, other_columns, normalized_prompt, matches)

    if not matches:
        return None

    # exact(1) 우선, 그다음 매칭 문자열 길이
    best_exact = max(exact for exact, _, _, _ in matches)
    candidates = [m for m in matches if m[0] == best_exact]
    longest = max(length for _, length, _, _ in candidates)

    mask = pd.Series(False, index=prepared.index)
    for exact, length, column, value in candidates:
        if exact != best_exact or length != longest:
            continue
        mask |= _column_equals(prepared[column], value)

    result = prepared.loc[mask]
    return result.reset_index(drop=True) if not result.empty else None


def _collect_value_matches(
    df: pd.DataFrame,
    columns: list,
    normalized_prompt: str,
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
            if not _value_mentioned_in_prompt(normalized, normalized_prompt):
                continue
            exact = 1 if _is_exact_value_mention(normalized, normalized_prompt) else 0
            matches.append((exact, len(normalized), str(column), value))


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
    preferred_columns = _mentioned_columns(prepared, normalized_prompt)
    matches: list[tuple[int, int, str, object]] = []

    search_columns = preferred_columns or list(prepared.columns)
    _collect_value_matches(prepared, search_columns, normalized_prompt, matches)
    if not matches and preferred_columns:
        other_columns = [c for c in prepared.columns if c not in preferred_columns]
        _collect_value_matches(prepared, other_columns, normalized_prompt, matches)

    if not matches:
        return None

    best_exact = max(exact for exact, _, _, _ in matches)
    candidates = [m for m in matches if m[0] == best_exact]
    longest = max(length for _, length, _, _ in candidates)
    for exact, length, column, value in candidates:
        if exact == best_exact and length == longest:
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
    """프롬프트에 언급된 수치형 컬럼을 모두 찾는다 (긴 이름 우선, 부분문자열 중복 제거)."""
    normalized_prompt = _normalize_text(prompt)
    scored: list[tuple[int, str]] = []
    for column in df.columns:
        coerced = pd.to_numeric(df[column], errors="coerce")
        if not coerced.notna().any() and not pd.api.types.is_numeric_dtype(df[column]):
            continue
        normalized = _normalize_text(str(column))
        if len(normalized) >= 2 and normalized in normalized_prompt:
            scored.append((len(normalized), column))

    if not scored:
        # 언급이 없으면 프롬프트의 컬럼명 부분일치만 재시도 (기존 단일 컬럼 경로와 동일)
        mentioned = _mentioned_columns(df, normalized_prompt)
        for column in mentioned:
            if column not in df.columns:
                continue
            coerced = pd.to_numeric(df[column], errors="coerce")
            if coerced.notna().any() or pd.api.types.is_numeric_dtype(df[column]):
                scored.append((len(_normalize_text(str(column))), column))

    if not scored:
        return []

    # 같은 길이면 프롬프트에 먼저 나온 컬럼을 앞에 둔다
    def prompt_pos(column: str) -> int:
        pos = normalized_prompt.find(_normalize_text(str(column)))
        return pos if pos >= 0 else 10**9

    scored.sort(key=lambda item: (-item[0], prompt_pos(item[1])))
    selected: list[str] = []
    selected_norms: list[str] = []
    for _length, column in scored:
        norm = _normalize_text(str(column))
        # 이미 고른 더 긴 이름에 포함되면 건너뛴다 (예: 예산 ⊂ 계획예산)
        if any(norm != prev and norm in prev for prev in selected_norms):
            continue
        selected.append(column)
        selected_norms.append(norm)
    return selected


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
    if not metric_cols:
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

