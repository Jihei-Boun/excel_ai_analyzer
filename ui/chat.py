"""채팅 요청 처리 로직."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.analyzer import run_analysis
from core.excel_loader import sanitize_dataframe


def process_user_prompt(prompt: str, *, user_already_added: bool = False) -> None:
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

    if not user_already_added:
        st.session_state.chat_messages.append({"role": "user", "content": prompt})

    with st.spinner("분석 중..."):
        try:
            reply, extra_df = _run_prompt(
                prompt,
                df,
            )
        except Exception as exc:
            reply = f"오류가 발생했습니다: {exc}"
            extra_df = None
            st.session_state.operation_result = None

    message: dict = {"role": "assistant", "content": reply}
    if extra_df is not None:
        message["dataframe"] = extra_df
    st.session_state.chat_messages.append(message)
    st.rerun()


def _run_prompt(
    prompt: str,
    df: pd.DataFrame,
) -> tuple[str, pd.DataFrame | None]:
    base_url = st.session_state.ollama_base_url
    model = st.session_state.ollama_model
    source = st.session_state.get("selected_df")
    if source is None:
        source = df

    result, summary = run_analysis(
        source,
        prompt,
        base_url=base_url,
        model=model,
    )

    if isinstance(result, pd.DataFrame):
        result = result.reset_index(drop=True)
        st.session_state.selected_df = result
        st.session_state.operation_result = None
        st.session_state.work_target = "분석 결과"
        st.session_state.active_operation = None
        return summary, result

    st.session_state.operation_result = result
    st.session_state.active_operation = "PandasAI"
    return summary, None
