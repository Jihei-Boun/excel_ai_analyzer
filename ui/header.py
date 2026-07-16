"""상단 헤더."""

from __future__ import annotations

import streamlit as st


def render_header() -> None:
    target = st.session_state.get("work_target", "원본 df")
    rows = _target_row_count()

    left, right = st.columns([3, 2])
    with left:
        st.markdown(
            f"""
            <div class="app-header-left" style="margin-bottom:0.75rem;">
                <div class="app-logo">EA</div>
                <div>
                    <p class="app-title">Excel AI Analyzer</p>
                    <p class="app-subtitle">엑셀 데이터를 자연어로 분석합니다</p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        r1, r2 = st.columns([2.2, 1])
        with r1:
            file_name = st.session_state.get("file_name") or "파일 없음"
            st.markdown(
                f'<div style="display:flex;justify-content:flex-end;padding-top:0.35rem;">'
                f'<span class="status-pill">{file_name} · {target} · {rows:,}행</span></div>',
                unsafe_allow_html=True,
            )
        with r2:
            if st.button("초기화", use_container_width=True):
                st.session_state.selected_df = None
                st.session_state.operation_result = None
                st.session_state.work_target = "원본 df"
                st.session_state.active_operation = None
                st.rerun()

    st.markdown(
        '<hr style="border:none;border-top:1px solid #2a3347;margin:0 0 1rem 0;">',
        unsafe_allow_html=True,
    )


def _target_row_count() -> int:
    selected = st.session_state.get("selected_df")
    if selected is not None:
        return len(selected)
    df = st.session_state.get("df")
    if df is not None:
        return len(df)
    return 0
