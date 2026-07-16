"""데이터 미리보기."""

from __future__ import annotations

import streamlit as st

from core.excel_loader import load_excel, sanitize_dataframe
from ui.display import for_display
from ui.upload import find_file


def render_preview_section() -> None:
    df = st.session_state.get("df")
    if df is None:
        st.info("엑셀 파일을 업로드하면 미리보기가 표시됩니다.")
        return

    file_name = st.session_state.get("file_name")
    if file_name:
        st.markdown(
            f'<p class="meta-line">미리보기 · {file_name}</p>',
            unsafe_allow_html=True,
        )

    # Arrow/비목섹션 호환을 위해 세션 데이터 정리
    if "비목섹션" not in df.columns or not st.session_state.get("_df_sanitized"):
        df = sanitize_dataframe(df)
        st.session_state.df = df
        file_id = st.session_state.get("active_file_id")
        if file_id:
            st.session_state.setdefault("file_frames", {})[file_id] = df
        st.session_state._df_sanitized = True

    sheet_names = st.session_state.get("sheet_names") or []
    # 시트가 여러 개일 때만 선택 UI 표시
    if len(sheet_names) > 1:
        selected = st.selectbox(
            "시트",
            sheet_names,
            index=sheet_names.index(st.session_state.current_sheet)
            if st.session_state.current_sheet in sheet_names
            else 0,
        )
        if selected != st.session_state.current_sheet:
            _switch_sheet(selected)

    height = min(900, max(280, 38 * (len(df) + 1)))
    st.dataframe(
        for_display(df),
        use_container_width=True,
        height=height,
        hide_index=True,
    )
    _render_summary_cards(df)


def _switch_sheet(sheet_name: str) -> None:
    file_id = st.session_state.get("active_file_id")
    path = st.session_state.get("file_path")
    if not path:
        return

    df = load_excel(path, sheet_name=sheet_name)
    st.session_state.df = df
    st.session_state.current_sheet = sheet_name
    st.session_state.selected_df = None
    st.session_state.operation_result = None
    st.session_state._df_sanitized = True

    if file_id:
        meta = find_file(file_id)
        if meta is not None:
            meta["current_sheet"] = sheet_name
        st.session_state.setdefault("file_frames", {})[file_id] = df

    st.rerun()


def _render_summary_cards(df) -> None:
    numeric_cols = df.select_dtypes(include="number").shape[1]
    string_cols = df.select_dtypes(include=["object", "string"]).shape[1]
    completeness = (1 - df.isna().sum().sum() / max(df.size, 1)) * 100

    st.markdown(
        f"""
        <div class="stat-grid">
            <div class="stat-card">
                <div class="label">총 행</div>
                <div class="value">{len(df):,}</div>
            </div>
            <div class="stat-card">
                <div class="label">총 열</div>
                <div class="value">{len(df.columns)}</div>
            </div>
            <div class="stat-card">
                <div class="label">수치형 컬럼</div>
                <div class="value">{numeric_cols}</div>
            </div>
            <div class="stat-card">
                <div class="label">문자형 컬럼</div>
                <div class="value">{string_cols}</div>
            </div>
            <div class="stat-card">
                <div class="label">데이터 완전성</div>
                <div class="value">{completeness:.1f}%</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
