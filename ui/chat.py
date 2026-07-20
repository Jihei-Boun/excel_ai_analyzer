"""채팅 요청 처리 로직."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.analyzer import (
    _expects_dataframe,
    _filter_by_mentioned_value,
    _filter_multi_by_mentioned_value,
    _is_complex_analysis,
    _normalize_text,
    build_context_aggregate_table,
    build_multi_context_aggregate_table,
    detect_aggregate_op,
    extract_matched_value,
    infer_context_label,
    run_analysis,
    run_multi_analysis,
    scalar_to_context_table,
    split_frames_by_source,
)
from core.excel_loader import sanitize_dataframe
from core.result_format import exclude_aggregate_rows, to_list_display
from ui.upload import get_active_named_frames, get_analysis_df, is_multi_analysis_mode


def process_user_prompt(prompt: str, *, user_already_added: bool = False) -> None:
    if is_multi_analysis_mode():
        named_frames = get_active_named_frames()
        if len(named_frames) < 2:
            st.warning("동시 분석 모드에서는 파일 2개 이상을 선택하세요.")
            return
        if not user_already_added:
            st.session_state.chat_messages.append({"role": "user", "content": prompt})

        with st.spinner(f"{len(named_frames)}개 파일 동시 분석 중..."):
            try:
                reply, extra_df, extra_meta = _run_multi_prompt(prompt, named_frames)
            except Exception as exc:
                reply = f"오류가 발생했습니다: {exc}"
                extra_df = None
                extra_meta = {}
                st.session_state.operation_result = None
    else:
        df: pd.DataFrame | None = get_analysis_df()
        if df is None:
            st.warning("먼저 엑셀 파일을 업로드하세요.")
            return

        df = sanitize_dataframe(df)
        active_id = st.session_state.get("active_file_id")
        if active_id:
            st.session_state.setdefault("file_frames", {})[active_id] = df
        st.session_state.df = df
        selected = st.session_state.get("selected_df")
        if selected is not None:
            st.session_state.selected_df = sanitize_dataframe(selected)
        filter_df = st.session_state.get("analysis_filter_df")
        if filter_df is not None:
            st.session_state.analysis_filter_df = sanitize_dataframe(filter_df)

        if not user_already_added:
            st.session_state.chat_messages.append({"role": "user", "content": prompt})

        with st.spinner("분석 중..."):
            try:
                reply, extra_df, extra_meta = _run_prompt(prompt, df)
            except Exception as exc:
                reply = f"오류가 발생했습니다: {exc}"
                extra_df = None
                extra_meta = {}
                st.session_state.operation_result = None

    message: dict = {"role": "assistant", "content": reply}
    message.update(extra_meta)
    if extra_df is not None:
        message["dataframe"] = extra_df
    st.session_state.chat_messages.append(message)
    st.rerun()


def _run_prompt(
    prompt: str,
    df: pd.DataFrame,
) -> tuple[str, pd.DataFrame | None, dict]:
    base_url = st.session_state.ollama_base_url
    model = st.session_state.ollama_model
    source = _resolve_analysis_source(df, prompt)
    context_label = _resolve_context_label(source, prompt)

    # 필터된 목록 위에서 합계/평균 등을 요청하면 요약 표로 바로 응답
    contextual = build_context_aggregate_table(
        source,
        prompt,
        context_label=context_label,
    )
    if contextual is not None:
        table, summary = contextual
        # 집계 결과는 채팅에만 붙이고, 선택/필터 데이터는 유지한다.
        reply, result = _store_dataframe_result(
            table,
            summary,
            keep_as_filter=False,
            replace_selection=False,
        )
        return reply, result, {}

    result, summary = run_analysis(
        source,
        prompt,
        base_url=base_url,
        model=model,
    )

    if (
        isinstance(result, pd.DataFrame)
        and result.empty
        and source is not df
        and len(df) > 0
    ):
        result, summary = run_analysis(
            df,
            prompt,
            base_url=base_url,
            model=model,
        )

    if isinstance(result, pd.DataFrame):
        result = result.reset_index(drop=True)
        _update_context_from_filter(df, prompt, result)
        result, summary, meta = _postprocess_table_result(
            result,
            prompt,
            summary,
            source_df=source,
        )
        is_filter = detect_aggregate_op(prompt) is None
        reply, stored = _store_dataframe_result(
            result,
            summary,
            keep_as_filter=is_filter,
            replace_selection=True,
        )
        return reply, stored, meta

    # 숫자만 온 경우에도 맥락 요약 표로 변환
    if detect_aggregate_op(prompt) is not None:
        table = scalar_to_context_table(
            result,
            prompt,
            source,
            context_label=context_label,
        )
        if table is not None:
            reply, stored = _store_dataframe_result(
                table,
                summary or f"{context_label or '합계'} 집계 결과",
                keep_as_filter=False,
                replace_selection=False,
            )
            return reply, stored, {}

    st.session_state.operation_result = result
    st.session_state.active_operation = "PandasAI"
    return summary, None, {}


def _postprocess_table_result(
    result: pd.DataFrame,
    prompt: str,
    summary: str,
    *,
    source_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, str, dict]:
    """집계 행 제거·리스트 표시 메타를 적용한다."""
    meta: dict = {}
    if detect_aggregate_op(prompt) is None:
        result, excluded = exclude_aggregate_rows(result, prompt)
        if excluded:
            summary = f"{summary} · 합계·소계 {excluded}행 제외"

    list_info = to_list_display(result, prompt, source_df=source_df)
    if list_info is not None:
        meta["list_values"] = list_info.values
        meta["list_label"] = list_info.label
        if list_info.groups:
            meta["list_groups"] = list_info.groups

    return result, summary, meta


def _store_dataframe_result(
    result: pd.DataFrame,
    summary: str,
    *,
    keep_as_filter: bool = False,
    replace_selection: bool = True,
) -> tuple[str, pd.DataFrame]:
    result = result.reset_index(drop=True)
    if keep_as_filter:
        st.session_state.analysis_filter_df = result
        st.session_state.selected_df = result
    elif replace_selection:
        st.session_state.selected_df = result
    else:
        # 집계 등: 필터/선택 데이터는 그대로 두고 결과 표만 채팅에 전달
        filter_df = st.session_state.get("analysis_filter_df")
        if filter_df is not None and len(filter_df) > 0:
            st.session_state.selected_df = filter_df
    st.session_state.operation_result = None
    st.session_state.work_target = "분석 결과" if not result.empty else "원본 df"
    st.session_state.active_operation = None
    return summary, result


def _update_context_from_filter(
    full_df: pd.DataFrame,
    prompt: str,
    result: pd.DataFrame,
) -> None:
    """리스트/필터 결과에서 다음 집계용 맥락 라벨을 저장한다."""
    if detect_aggregate_op(prompt) is not None:
        return
    if result is None or result.empty:
        return

    label = infer_context_label(prompt=prompt, result_df=result, full_df=full_df)
    if label:
        st.session_state.analysis_context_label = label
        st.session_state.last_filter_summary = label


def _resolve_context_label(source: pd.DataFrame, prompt: str) -> str | None:
    """집계 표의 행 라벨: 저장된 필터명 → 필터 표의 분류값 → 이전 질문."""
    stored = st.session_state.get("analysis_context_label")
    if stored:
        return str(stored)

    filter_df = st.session_state.get("analysis_filter_df")
    work = filter_df if filter_df is not None and len(filter_df) > 0 else source

    # 현재 집계 질문이 아니라, 필터 결과/이전 질문에서만 라벨을 뽑는다
    label = infer_context_label(prompt=None, result_df=work, full_df=None)
    if label:
        st.session_state.analysis_context_label = label
        return label

    for message in reversed(st.session_state.get("chat_messages") or []):
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        if detect_aggregate_op(content):
            continue
        label = infer_context_label(
            prompt=content,
            result_df=work,
            full_df=source,
        )
        if label:
            st.session_state.analysis_context_label = label
            return label
    return None


def _run_multi_prompt(
    prompt: str,
    named_frames: list[tuple[str, pd.DataFrame]],
) -> tuple[str, pd.DataFrame | None, dict]:
    base_url = st.session_state.ollama_base_url
    model = st.session_state.ollama_model
    prepared = [(name, sanitize_dataframe(df)) for name, df in named_frames]

    # 집계: 이전 필터(연구활동비 등) 기준으로 파일별 요약 표
    if detect_aggregate_op(prompt) is not None:
        source_named, context_label = _resolve_multi_aggregate_source(prepared, prompt)
        contextual = build_multi_context_aggregate_table(
            source_named,
            prompt,
            context_label=context_label,
        )
        if contextual is not None:
            table, summary = contextual
            reply, stored = _store_dataframe_result(
                table,
                summary,
                keep_as_filter=False,
                replace_selection=False,
            )
            return reply, stored, {}

    result, summary = run_multi_analysis(
        prepared,
        prompt,
        base_url=base_url,
        model=model,
    )

    if isinstance(result, pd.DataFrame):
        result = result.reset_index(drop=True)
        is_filter = detect_aggregate_op(prompt) is None
        if is_filter:
            _update_context_from_filter(result, prompt, result)
        multi_source = st.session_state.get("analysis_filter_df")
        if multi_source is None or len(multi_source) == 0:
            parts = []
            for name, frame in prepared:
                part = frame.copy()
                part.insert(0, "출처파일", name)
                parts.append(part)
            multi_source = pd.concat(parts, ignore_index=True) if parts else None
        result, summary, meta = _postprocess_table_result(
            result,
            prompt,
            summary,
            source_df=multi_source,
        )
        reply, stored = _store_dataframe_result(
            result,
            summary,
            keep_as_filter=is_filter,
            replace_selection=True,
        )
        return reply, stored, meta

    st.session_state.operation_result = result
    st.session_state.active_operation = "PandasAI (다중)"
    return summary, None, {}


def _resolve_multi_aggregate_source(
    prepared: list[tuple[str, pd.DataFrame]],
    prompt: str,
) -> tuple[list[tuple[str, pd.DataFrame]], str | None]:
    """집계에 쓸 파일별 데이터와 행 맥락 라벨을 결정한다."""
    context_label = st.session_state.get("analysis_context_label")
    filter_df = st.session_state.get("analysis_filter_df")

    # 프롬프트에 새 분류값이 있으면 그걸로 다시 필터 (이전 필터와 다르면)
    prompt_filtered = _filter_multi_by_mentioned_value(prepared, prompt)
    prompt_label = None
    if prompt_filtered is not None and not prompt_filtered.empty:
        prompt_label = infer_context_label(
            prompt=prompt,
            result_df=prompt_filtered,
            full_df=None,
        ) or extract_matched_value(prompt_filtered, prompt)

    reuse_filter = (
        filter_df is not None
        and len(filter_df) > 0
        and "출처파일" in filter_df.columns
    )
    if reuse_filter and prompt_label and context_label:
        if _normalize_text(str(prompt_label)) != _normalize_text(str(context_label)):
            reuse_filter = False
    if reuse_filter and prompt_label and prompt_filtered is not None:
        # 이전 필터에 해당 값이 없으면 갱신
        on_filter = _filter_by_mentioned_value(filter_df, prompt)
        if on_filter is None or on_filter.empty:
            reuse_filter = False

    if reuse_filter:
        parts = split_frames_by_source(filter_df)
        if parts:
            if not context_label:
                context_label = infer_context_label(
                    prompt=None,
                    result_df=filter_df,
                    full_df=None,
                )
                if context_label:
                    st.session_state.analysis_context_label = context_label
            return parts, str(context_label) if context_label else None

    if prompt_filtered is not None and not prompt_filtered.empty:
        label = prompt_label or infer_context_label(
            prompt=prompt,
            result_df=prompt_filtered,
            full_df=None,
        )
        st.session_state.analysis_filter_df = prompt_filtered
        st.session_state.selected_df = prompt_filtered
        if label:
            st.session_state.analysis_context_label = label
            context_label = label
        return split_frames_by_source(prompt_filtered), (
            str(context_label) if context_label else None
        )

    if not context_label:
        context_label = infer_context_label(prompt=prompt, result_df=None, full_df=None)
    return prepared, str(context_label) if context_label else None


def _resolve_analysis_source(df: pd.DataFrame, prompt: str) -> pd.DataFrame:
    """이전 필터 결과에 없는 값을 요청하면 원본 DataFrame으로 되돌린다."""
    filter_df = st.session_state.get("analysis_filter_df")
    selected = st.session_state.get("selected_df")

    # 집계는 직전 필터 목록을 유지한 채 계산
    if detect_aggregate_op(prompt) is not None:
        if filter_df is not None and len(filter_df) > 0:
            return filter_df
        if selected is not None and len(selected) > 0:
            return selected
        return df

    if selected is None or len(selected) == 0:
        if selected is not None and len(selected) == 0:
            st.session_state.selected_df = None
            st.session_state.work_target = "원본 df"
        return df

    work = filter_df if filter_df is not None and len(filter_df) > 0 else selected

    if _expects_dataframe(prompt) and not _is_complex_analysis(prompt):
        on_selected = _filter_by_mentioned_value(work, prompt)
        on_full = _filter_by_mentioned_value(df, prompt)
        if (on_selected is None or on_selected.empty) and (
            on_full is not None and not on_full.empty
        ):
            st.session_state.selected_df = None
            st.session_state.analysis_filter_df = None
            st.session_state.work_target = "원본 df"
            return df

    return work
