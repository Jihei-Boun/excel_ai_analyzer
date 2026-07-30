"""상단 헤더 — Streamlit 네이티브 컴포넌트."""

from __future__ import annotations

import streamlit as st

from ui.session_store import reset_work_state
from ui.upload import get_active_named_frames, is_multi_analysis_mode


def render_header() -> None:
    target = st.session_state.get("work_target", "원본 df")
    rows = _target_row_count()
    file_label = _file_status_label()

    title_col, action_col = st.columns([5, 1])
    with title_col:
        st.title("Excel AI Analyzer")
        st.caption("엑셀 데이터를 자연어로 분석합니다")
        st.caption(f"{file_label} · {target} · {rows:,}행")
    with action_col:
        # 제목 줄과 같은 높이에 두지 않고 버튼만 배치 (상단 툴바 겹침 방지)
        st.write("")
        if st.button("초기화", use_container_width=True):
            reset_work_state(clear_chat=True)
            st.rerun()

    st.divider()


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
