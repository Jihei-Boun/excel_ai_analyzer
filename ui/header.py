"""상단 헤더."""

from __future__ import annotations

import streamlit as st

from ui.session_store import reset_work_state
from ui.upload import get_active_named_frames, is_multi_analysis_mode


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
            file_label = _file_status_label()
            st.markdown(
                f'<div style="display:flex;justify-content:flex-end;padding-top:0.35rem;">'
                f'<span class="status-pill">{file_label} · {target} · {rows:,}행</span></div>',
                unsafe_allow_html=True,
            )
        with r2:
            if st.button("초기화", use_container_width=True):
                reset_work_state(clear_chat=True)
                st.rerun()

    st.markdown('<hr class="app-divider">', unsafe_allow_html=True)


def _file_status_label() -> str:
    if is_multi_analysis_mode():
        names = [name for name, _ in get_active_named_frames()]
        if not names:
            return "파일 없음"
        if len(names) <= 2:
            return " + ".join(names)
        return f"{names[0]} 외 {len(names) - 1}개"
    return st.session_state.get("file_name") or "파일 없음"


def _target_row_count() -> int:
    selected = st.session_state.get("selected_df")
    if selected is not None and len(selected) > 0:
        return len(selected)
    df = st.session_state.get("df")
    if df is not None:
        return len(df)
    return 0
