"""필터 소스 결정·맥락 라벨·필터 요약."""
from __future__ import annotations

import re

import pandas as pd

from core.routing.prompt_intent import (
    detect_aggregate_op,
    expects_plot as _expects_plot,
    wants_full_dataset,
)
from core.io.text_normalize import normalize_text
from core.filter.value_match import (
    _PROMPT_NOISE,
    _filter_by_mentioned_value,
    extract_matched_detail,
    extract_matched_value,
)

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

    # '전체 데이터에서 …' / '필터 초기화' 등 명시적 전체 분석 요청
    if wants_full_dataset(prompt):
        had_filter = filtered_df is not None and len(filtered_df) > 0
        return full_df, had_filter

    if keep_filter_for_aggregate and (
        detect_aggregate_op(prompt) is not None
        or _is_groupby_prompt(prompt)
    ):
        if filtered_df is not None and len(filtered_df) > 0:
            if _should_reset_filter_for_groupby(full_df, filtered_df, prompt):
                return full_df, True
            return filtered_df, False
        return full_df, False

    if _expects_plot(prompt):
        if filtered_df is not None and len(filtered_df) > 0:
            if _should_reset_filter_for_groupby(full_df, filtered_df, prompt):
                return full_df, True
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


def _is_groupby_prompt(prompt: str) -> bool:
    from core.schema.column_match import _is_explicit_groupby_prompt

    return _is_explicit_groupby_prompt(prompt)


def _should_reset_filter_for_groupby(
    full_df: pd.DataFrame,
    filtered_df: pd.DataFrame,
    prompt: str,
) -> bool:
    """'담당자별'처럼 그룹 집계인데 필터가 그룹을 1개로 붕괴시키면 원본으로 돌린다.

    예: 결측 행 1건(담당자=최유나) 이후 '담당자별 집계' → 전체 데이터로 집계.
    """
    from core.schema.column_match import find_groupby_column

    if not _is_groupby_prompt(prompt):
        return False
    if len(filtered_df) >= len(full_df):
        return False

    group_col = find_groupby_column(full_df, prompt)
    if not group_col or group_col not in filtered_df.columns:
        return False

    n_full = int(full_df[group_col].nunique(dropna=True))
    n_filt = int(filtered_df[group_col].nunique(dropna=True))
    return n_full >= 2 and n_filt <= 1


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


def format_context_label(
    label: str | None,
    *,
    profile_name: str | None = None,
) -> str:
    """표시용 행 라벨. '연구활동비항목' → '연구활동비'처럼 꼬리표 정리."""
    if not label:
        from core.profile_loader import locale_for

        return "Total" if locale_for(profile_name=profile_name) == "en" else "합계"
    text = str(label).strip()
    text = re.sub(r"(항목|목록|리스트|내역)$", "", text).strip()
    return text or str(label).strip()


def infer_context_label(
    *,
    prompt: str | None = None,
    result_df: pd.DataFrame | None = None,
    full_df: pd.DataFrame | None = None,
    allow_prompt_text: bool = True,
) -> str | None:
    """필터/목록 요청에서 행 라벨을 여러 단서로 추론한다.

    우선순위:
    1) 결과 표에서 값이 하나뿐인 문자 컬럼
    2) full_df/result_df 셀 값과 프롬프트 매칭
    3) 프롬프트에서 잡음 단어를 제거한 핵심 구 (allow_prompt_text=True일 때만)
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
    if allow_prompt_text and prompt:
        from_prompt = _label_from_prompt_text(prompt)
        if from_prompt:
            return from_prompt
    return None


_LABEL_REJECT_TOKENS = (
    "컬럼",
    "의미",
    "추측",
    "설명",
    "품질",
    "수정",
    "타입",
    "구분",
    "숫자",
    "문자",
    "날짜",
    "스키마",
    "결측",
    "분석전",
    "전처리",
    "문제",
    "개선",
    "요약",
    "비교",
    "병합",
    "차트",
    "그래프",
)


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
    # 너무 긴 문장/메타 잔여물은 제외
    if len(text) > 24:
        return None
    lowered = text.lower()
    if any(token in lowered for token in _LABEL_REJECT_TOKENS):
        return None
    return text
