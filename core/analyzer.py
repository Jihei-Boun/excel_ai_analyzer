"""자연어 요청을 PandasAI로 실행하는 범용 분석 진입점."""

from __future__ import annotations

import re

import pandas as pd

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


def run_analysis(
    df: pd.DataFrame,
    prompt: str,
    *,
    base_url: str,
    model: str,
) -> tuple[object, str]:
    """DataFrame과 사용자 요청을 PandasAI에 전달해 결과를 반환한다."""
    if not prompt.strip():
        raise ValueError("분석 요청을 입력해 주세요.")

    output_type = "dataframe" if _expects_dataframe(prompt) else None

    # 병합 셀·분류 필터처럼 단순 목록 요청은 LLM보다 값 일치가 안정적이다.
    if output_type == "dataframe" and not _is_complex_analysis(prompt):
        direct = _filter_by_mentioned_value(df, prompt)
        if direct is not None and not direct.empty:
            return (
                direct,
                f"데이터 값 일치 결과: {len(direct):,}행",
            )

    query = (
        "사용자의 요청을 현재 DataFrame의 실제 컬럼명과 데이터 타입에 맞춰 "
        "pandas 연산으로 수행하세요.\n"
        "필터링, 정렬, 집계, 그룹화, 피벗, 통계 등 요청의 종류를 스스로 판단하세요.\n"
        "특정 컬럼이나 데이터 형식을 가정하지 마세요.\n"
        "반복된 상위 분류 값은 원본의 빈 상세 행을 분석용으로 채운 값이므로 "
        "같은 분류의 모든 행을 필터링할 때 사용하세요.\n"
        "'리스트', '목록', '표', '보여줘' 요청은 반드시 DataFrame으로 반환하세요.\n"
        "합계·총합·평균 등 집계 요청도 가능하면 "
        "행 라벨(분류명)과 컬럼명·집계값으로 된 작은 DataFrame으로 반환하세요.\n"
        "예: 열이 ['', '계획예산']이고 행이 ['연구활동비', 12345] 형태.\n"
        "그 외 단일 계산만 숫자나 문자열로 반환하세요.\n"
        f"사용자 요청: {prompt}"
    )
    try:
        result, summary = chat(
            df,
            query,
            base_url=base_url,
            model=model,
            output_type=output_type,
        )
    except RuntimeError:
        if output_type == "dataframe":
            fallback = _filter_by_mentioned_value(df, prompt)
            if fallback is not None and not fallback.empty:
                return (
                    fallback,
                    f"데이터 값 일치 결과: {len(fallback):,}행",
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
            )
    return result, summary


def run_multi_analysis(
    named_dfs: list[tuple[str, pd.DataFrame]],
    prompt: str,
    *,
    base_url: str,
    model: str,
) -> tuple[object, str]:
    """여러 DataFrame을 SmartDatalake로 동시에 분석한다."""
    if len(named_dfs) < 2:
        raise ValueError("동시 분석에는 파일 2개 이상이 필요합니다.")
    if not prompt.strip():
        raise ValueError("분석 요청을 입력해 주세요.")

    output_type = "dataframe" if _expects_dataframe(prompt) else None

    # 분류값 목록처럼 단순 요청은 파일별 값 일치 후 합친다.
    if output_type == "dataframe" and not _is_complex_analysis(prompt):
        direct = _filter_multi_by_mentioned_value(named_dfs, prompt)
        if direct is not None and not direct.empty:
            file_count = direct["출처파일"].nunique() if "출처파일" in direct.columns else len(named_dfs)
            return (
                direct,
                f"데이터 값 일치 결과: {len(direct):,}행 ({file_count}개 파일)",
            )

    file_names = ", ".join(name for name, _ in named_dfs)
    query = (
        "여러 엑셀 파일의 DataFrame이 동시에 제공됩니다. "
        "각 dfs[i]는 서로 다른 파일이며, 파일명과 테이블명을 참고하세요.\n"
        "비교, 병합(merge/join), 교차 집계, 공통 컬럼 탐색 등 요청을 판단해 pandas로 수행하세요.\n"
        "특정 컬럼이나 데이터 형식을 가정하지 마세요.\n"
        "반복된 상위 분류 값은 원본의 빈 상세 행을 분석용으로 채운 값이므로 "
        "같은 분류의 모든 행을 필터링할 때 사용하세요.\n"
        "'리스트', '목록', '표', '보여줘' 요청은 반드시 DataFrame으로 반환하세요.\n"
        "단일 계산만 숫자나 문자열로 반환하세요.\n"
        f"분석 대상 파일: {file_names}\n"
        f"사용자 요청: {prompt}"
    )
    try:
        result, summary = chat_multi(
            named_dfs,
            query,
            base_url=base_url,
            model=model,
            output_type=output_type,
        )
    except RuntimeError:
        if output_type == "dataframe":
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
                )
        raise

    if (
        output_type == "dataframe"
        and isinstance(result, pd.DataFrame)
        and result.empty
    ):
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
            )
    return result, summary


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


def _is_complex_analysis(prompt: str) -> bool:
    """집계·순위·시각화처럼 단순 값 필터로 해결할 수 없는 요청인지 판별한다."""
    lowered = prompt.lower()
    return any(keyword in lowered for keyword in _COMPLEX_KEYWORDS)


def _filter_by_mentioned_value(
    df: pd.DataFrame,
    prompt: str,
) -> pd.DataFrame | None:
    """요청에 명시된 실제 셀 값으로 행을 찾는 범용 폴백."""
    prepared = prepare_dataframe_for_ai(df)
    normalized_prompt = _normalize_text(prompt)
    preferred_columns = _mentioned_columns(prepared, normalized_prompt)
    matches: list[tuple[int, str, object]] = []

    search_columns = preferred_columns or list(prepared.columns)
    for column in search_columns:
        series = prepared[column]
        if not (pd.api.types.is_string_dtype(series) or series.dtype == object):
            continue
        for value in series.dropna().unique():
            text = str(value).strip()
            if not text:
                continue
            normalized = _normalize_text(text)
            if len(normalized) >= 2 and normalized in normalized_prompt:
                matches.append((len(normalized), column, value))

    if not matches and preferred_columns:
        # 컬럼은 언급됐지만 값 매칭이 없으면 전체 컬럼으로 재시도
        for column in prepared.columns:
            if column in preferred_columns:
                continue
            series = prepared[column]
            if not (pd.api.types.is_string_dtype(series) or series.dtype == object):
                continue
            for value in series.dropna().unique():
                text = str(value).strip()
                if not text:
                    continue
                normalized = _normalize_text(text)
                if len(normalized) >= 2 and normalized in normalized_prompt:
                    matches.append((len(normalized), column, value))

    if not matches:
        return None

    longest = max(length for length, _, _ in matches)
    mask = pd.Series(False, index=prepared.index)
    for length, column, value in matches:
        if length == longest:
            mask |= prepared[column].astype(str) == str(value)

    result = prepared.loc[mask]
    return result.reset_index(drop=True) if not result.empty else None


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
    ("합계", "sum"),
    ("합을", "sum"),
    ("합쳐", "sum"),
    ("sum", "sum"),
    ("total", "sum"),
)


def detect_aggregate_op(prompt: str) -> str | None:
    """프롬프트에서 집계 연산 종류를 찾는다. 없으면 None."""
    lowered = prompt.lower()
    normalized = _normalize_text(prompt)
    for keyword, op in _AGGREGATE_OPS:
        if keyword.lower() in lowered or _normalize_text(keyword) in normalized:
            return op
    return None


def extract_matched_value(df: pd.DataFrame, prompt: str) -> str | None:
    """요청 문구에 등장하는 실제 셀 값(가장 긴 일치)을 반환한다."""
    prepared = prepare_dataframe_for_ai(df)
    normalized_prompt = _normalize_text(prompt)
    preferred_columns = _mentioned_columns(prepared, normalized_prompt)
    matches: list[tuple[int, object]] = []

    search_columns = preferred_columns or list(prepared.columns)
    for column in search_columns:
        series = prepared[column]
        if not (pd.api.types.is_string_dtype(series) or series.dtype == object):
            continue
        for value in series.dropna().unique():
            text = str(value).strip()
            if not text:
                continue
            normalized = _normalize_text(text)
            if len(normalized) >= 2 and normalized in normalized_prompt:
                matches.append((len(normalized), text))

    if not matches:
        return None
    longest = max(length for length, _ in matches)
    for length, text in matches:
        if length == longest:
            return text
    return None


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
    op = detect_aggregate_op(prompt)
    if op is None or df is None or df.empty:
        return None

    metric_cols = find_mentioned_numeric_columns(df, prompt)
    if not metric_cols:
        return None

    if op == "mean":
        op_name = "평균"
        reduce = lambda s: float(s.mean())
    elif op == "max":
        op_name = "최댓값"
        reduce = lambda s: float(s.max())
    elif op == "min":
        op_name = "최솟값"
        reduce = lambda s: float(s.min())
    else:
        op_name = "총합"
        reduce = lambda s: float(s.sum())

    row_label = format_context_label(context_label)
    row: dict[str, object] = {"": row_label}
    summary_parts: list[str] = []

    for metric_col in metric_cols:
        series = pd.to_numeric(df[metric_col], errors="coerce")
        value = reduce(series)
        if pd.isna(value):
            continue
        row[str(metric_col)] = value
        summary_parts.append(f"{metric_col} {op_name}: {value:,.0f}")

    if len(row) <= 1:
        return None

    table = pd.DataFrame([row])
    summary = f"{row_label} · " + " / ".join(summary_parts)
    return table, summary


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

