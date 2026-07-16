"""우측 AI 분석 채팅 패널."""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from ui.chat import process_user_prompt
from ui.display import for_display
from ui.session_store import QUICK_OPERATIONS, RECOMMENDED_PROMPTS


def render_chat_panel() -> None:
    st.markdown('<p class="panel-title">AI 분석</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="panel-desc">자연어로 필터·합계·정렬을 요청하세요.</p>',
        unsafe_allow_html=True,
    )

    _render_chat_history()
    _render_selected_result()
    _render_operation_result()
    _render_quick_operations()
    _render_chat_input()


def _render_chat_history() -> None:
    messages = st.session_state.chat_messages
    if not messages:
        st.caption("아직 대화가 없습니다. 아래 예시로 시작해 보세요.")
        cols = st.columns(2)
        for idx, prompt in enumerate(RECOMMENDED_PROMPTS[:4]):
            with cols[idx % 2]:
                if st.button(prompt, key=f"ex_{idx}", use_container_width=True):
                    st.session_state.pending_prompt = prompt
                    st.rerun()
        return

    for message in messages:
        role = message["role"]
        css = "chat-user" if role == "user" else "chat-assistant"
        st.markdown(f'<div class="{css}">{message["content"]}</div>', unsafe_allow_html=True)

        if message.get("filter_summary"):
            st.markdown(
                f'<div class="filter-ok">✓ {message["filter_summary"]}</div>',
                unsafe_allow_html=True,
            )


def _render_selected_result() -> None:
    selected = st.session_state.get("selected_df")
    if selected is None:
        return

    st.caption(f"선택 결과 · {len(selected):,}행")
    st.dataframe(
        for_display(selected.head(10)),
        use_container_width=True,
        hide_index=True,
        height=200,
    )


def _work_source() -> pd.DataFrame | None:
    """선택된 데이터가 있으면 그걸, 없으면 원본 df를 반환한다."""
    selected = st.session_state.get("selected_df")
    if selected is not None:
        return selected
    return st.session_state.get("df")


def _render_quick_operations() -> None:
    source = _work_source()
    if source is None:
        return

    with st.expander("빠른 연산", expanded=False):
        op_cols = st.columns(4)
        for idx, (label, op_id) in enumerate(QUICK_OPERATIONS):
            with op_cols[idx % 4]:
                if st.button(label, key=f"op_{op_id}", use_container_width=True):
                    st.session_state.active_operation = op_id
                    _run_quick_operation(op_id)
                    st.rerun()


def _run_quick_operation(op_id: str) -> None:
    source = _work_source()
    if source is None:
        return

    numeric_cols = source.select_dtypes(include="number").columns
    if op_id == "reset":
        st.session_state.selected_df = None
        st.session_state.operation_result = None
        st.session_state.work_target = "원본 df"
        st.session_state.active_operation = None
        return

    if len(numeric_cols) == 0:
        st.session_state.operation_result = "수치형 컬럼이 없습니다."
        return

    target_col = numeric_cols[0]
    if op_id == "sum":
        st.session_state.operation_result = float(source[target_col].sum())
    elif op_id == "mean":
        st.session_state.operation_result = float(source[target_col].mean())
    elif op_id == "max":
        st.session_state.operation_result = float(source[target_col].max())
    elif op_id == "min":
        st.session_state.operation_result = float(source[target_col].min())
    elif op_id == "sort":
        st.session_state.selected_df = source.sort_values(target_col, ascending=False)
        st.session_state.work_target = "selected_df"
    elif op_id == "topn":
        st.session_state.selected_df = source.nlargest(10, target_col)
        st.session_state.work_target = "selected_df"
    elif op_id == "filter":
        st.session_state.pending_prompt = f"{target_col} 상위 10개만 보여줘"


def _render_operation_result() -> None:
    result = st.session_state.get("operation_result")
    if result is None:
        return

    label = st.session_state.get("active_operation", "result")
    if isinstance(result, pd.DataFrame):
        display = f"{len(result):,}행 × {len(result.columns)}열"
    else:
        try:
            display = f"{float(result):,.0f}"
        except (TypeError, ValueError):
            display = str(result)

    st.markdown(
        f"""
        <div class="result-box">
            <div class="label">결과 · {label}</div>
            <div class="value">{display}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    export_df = result if isinstance(result, pd.DataFrame) else st.session_state.get("selected_df")
    if export_df is not None:
        buffer = io.BytesIO()
        export_df.to_excel(buffer, index=False, engine="openpyxl")
        st.download_button(
            "Excel 다운로드",
            data=buffer.getvalue(),
            file_name="analysis_result.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


def _render_chat_input() -> None:
    st.markdown("---")
    default = st.session_state.pop("pending_prompt", "") or ""
    prompt = st.text_area(
        "질문 입력",
        value=default,
        placeholder="예: 예산잔액이 500만원 이상인 항목만 보여줘",
        label_visibility="collapsed",
        height=72,
        key="chat_prompt_input",
    )

    if st.button("전송", type="primary", use_container_width=True):
        if not prompt.strip():
            return
        if st.session_state.get("df") is None:
            st.warning("먼저 엑셀 파일을 업로드하세요.")
            return
        process_user_prompt(prompt.strip())
