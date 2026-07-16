"""채팅 요청 처리 로직."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.excel_loader import sanitize_dataframe
from core.operator import run_operation
from core.prompt_router import classify_intent
from core.selector import run_selection


def process_user_prompt(prompt: str) -> None:
    df: pd.DataFrame | None = st.session_state.get("df")
    if df is None:
        st.warning("먼저 엑셀 파일을 업로드하세요.")
        return

    # 이전 세션/미전처리 데이터도 안전하게 정리
    df = sanitize_dataframe(df)
    st.session_state.df = df
    selected = st.session_state.get("selected_df")
    if selected is not None:
        st.session_state.selected_df = sanitize_dataframe(selected)

    st.session_state.chat_messages.append({"role": "user", "content": prompt})

    with st.spinner("분석 중..."):
        try:
            intent = classify_intent(
                prompt,
                base_url=st.session_state.ollama_base_url,
                model=st.session_state.intent_model,
            )
            reply, extra_df, filter_summary = _handle_intent(intent, prompt, df)
        except Exception as exc:
            reply = f"오류가 발생했습니다: {exc}"
            extra_df = None
            filter_summary = ""

    message: dict = {"role": "assistant", "content": reply}
    if filter_summary:
        message["filter_summary"] = filter_summary
    if extra_df is not None:
        message["dataframe"] = extra_df
    st.session_state.chat_messages.append(message)
    st.rerun()


def _handle_intent(
    intent: str,
    prompt: str,
    df: pd.DataFrame,
) -> tuple[str, pd.DataFrame | None, str]:
    base_url = st.session_state.ollama_base_url
    model = st.session_state.ollama_model

    if intent == "operation":
        source = st.session_state.get("selected_df")
        if source is None:
            source = df
        result, summary = run_operation(
            source,
            prompt,
            base_url=base_url,
            model=model,
        )
        st.session_state.operation_result = result
        st.session_state.active_operation = "llm"
        return summary, _as_dataframe(result), ""

    selected_df, summary = run_selection(
        df,
        prompt,
        base_url=base_url,
        model=model,
    )
    st.session_state.selected_df = selected_df
    st.session_state.work_target = "selected_df"
    st.session_state.last_filter_summary = summary
    return summary, selected_df, f"데이터 추출 완료: {len(selected_df)}행 · {summary}"


def _as_dataframe(result: object) -> pd.DataFrame | None:
    if isinstance(result, pd.DataFrame):
        return result
    return None
