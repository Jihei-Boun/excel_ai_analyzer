"""프롬프트 값 매칭 코어."""
from __future__ import annotations

import re

import pandas as pd

from core.schema.column_match import (
    _mentioned_columns,
    find_mentioned_numeric_column,
    resolve_metric_column,
)
from core.pai.pandasai_config import prepare_dataframe_for_ai
from core.routing.prompt_intent import (
    _match_aggregate_op,
    is_condition_filter_request,
)
from core.io.text_normalize import normalize_text

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

    조건 비교(==0, 있는/없는 등) 요청은 단순 값 일치로 처리하지 않는다.
    """
    if is_condition_filter_request(prompt):
        return None

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
    from core.pai.pandasai_config import is_total_label

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
    if find_mentioned_numeric_column(probe, prompt) is not None:
        return True
    from core.schema.column_match import list_numeric_metric_columns, wants_all_numeric_metrics

    return wants_all_numeric_metrics(prompt) and bool(list_numeric_metric_columns(probe))


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

