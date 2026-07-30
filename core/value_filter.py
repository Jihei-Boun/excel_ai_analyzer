"""프롬프트 값 매칭·필터·맥락 라벨."""

from __future__ import annotations

import re

import pandas as pd

from core.column_match import (
    _mentioned_columns,
    find_mentioned_numeric_column,
)
from core.pandasai_config import prepare_dataframe_for_ai
from core.prompt_intent import (
    _match_aggregate_op,
    detect_aggregate_op,
    expects_plot as _expects_plot,
)
from core.text_normalize import normalize_text

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
    normalized_prompt = normalize_text(prompt)
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
            normalized = normalize_text(text)
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
    normalized_tokens = [normalize_text(token) for token in tokens]
    return [
        token
        for token in normalized_tokens
        if len(token) >= 2 and token not in {normalize_text(n) for n in _PROMPT_NOISE}
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
        col_norm = normalize_text(str(column))
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


def extract_matched_value(df: pd.DataFrame, prompt: str) -> str | None:
    """요청 문구에 등장하는 실제 셀 값(가장 긴 일치)을 반환한다."""
    detail = extract_matched_detail(df, prompt)
    return detail[1] if detail else None


def extract_matched_detail(df: pd.DataFrame, prompt: str) -> tuple[str, str] | None:
    """요청 문구에 등장하는 실제 셀 값과 컬럼명을 (컬럼, 값)으로 반환한다."""
    prepared = prepare_dataframe_for_ai(df)
    normalized_prompt = normalize_text(prompt)
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
        if label and normalize_text(value) == normalize_text(str(label)):
            parts.append(f"{column} 일치")
        else:
            parts.append(f"{column}={value}")

    resolved_files = file_count
    if resolved_files is None and "출처파일" in result_df.columns:
        resolved_files = int(result_df["출처파일"].nunique())
    if resolved_files and resolved_files > 1:
        parts.append(f"{resolved_files}개 파일")

    return " · ".join(parts) if parts else None


def format_context_label(label: str | None) -> str:
    """표시용 행 라벨. '연구활동비항목' → '연구활동비'처럼 꼬리표 정리."""
    if not label:
        return "합계"
    text = str(label).strip()
    text = re.sub(r"(항목|목록|리스트|내역)$", "", text).strip()
    return text or str(label).strip()


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
