"""데이터 미리보기 — 분석 대상과 무관하게 업로드된 파일을 탐색한다."""

from __future__ import annotations

import streamlit as st

from core.excel_loader import load_excel, sanitize_dataframe
from ui.display import for_preview_display, preview_column_labels, render_dataframe
from ui.upload import get_preview_context, set_preview_file


def render_preview_section() -> None:
    files = st.session_state.get("uploaded_files") or []
    if not files:
        st.info("엑셀 파일을 업로드하면 미리보기가 표시됩니다.")
        return

    _render_preview_file_picker(files)

    preview_id, meta, df = get_preview_context()
    if preview_id is None or meta is None or df is None:
        st.info("미리볼 파일을 선택하세요.")
        return

    st.markdown(
        f'<p class="meta-line">미리보기 · {meta["name"]}</p>',
        unsafe_allow_html=True,
    )

    if not st.session_state.get("_preview_sanitized_ids"):
        st.session_state._preview_sanitized_ids = set()

    sanitized_ids: set[str] = st.session_state._preview_sanitized_ids
    if preview_id not in sanitized_ids:
        df = sanitize_dataframe(df)
        st.session_state.setdefault("file_frames", {})[preview_id] = df
        sanitized_ids.add(preview_id)
        # 분석 대상과 같으면 분석용 df도 동기화
        if preview_id == st.session_state.get("active_file_id"):
            st.session_state.df = df
            st.session_state._df_sanitized = True

    sheet_names = meta.get("sheet_names") or []
    current_sheet = meta.get("current_sheet")
    if len(sheet_names) > 1:
        selected = st.selectbox(
            "시트",
            sheet_names,
            index=sheet_names.index(current_sheet) if current_sheet in sheet_names else 0,
            key=f"preview_sheet_{preview_id}",
        )
        if selected != current_sheet:
            _switch_preview_sheet(preview_id, meta, selected)
            return

    height = min(900, max(280, 38 * (len(df) + 1)))
    column_labels = preview_column_labels(list(df.columns))
    column_config = {
        column: st.column_config.Column(label=label)
        for column, label in column_labels.items()
    }
    render_dataframe(
        for_preview_display(df),
        height=height,
        hide_index=True,
        column_config=column_config,
        column_labels=column_labels,
    )
    _render_summary_cards(df)


def _render_preview_file_picker(files: list[dict]) -> None:
    """업로드된 모든 파일 중 미리볼 파일을 고른다 (분석 대상과 무관)."""
    if len(files) < 2:
        # 파일이 하나면 그걸로 미리보기 고정
        only_id = files[0]["id"]
        if st.session_state.get("preview_file_id") != only_id:
            set_preview_file(only_id)
        return

    labels = {meta["id"]: meta["name"] for meta in files}
    options = [meta["id"] for meta in files]

    current = st.session_state.get("preview_file_id")
    if current not in options:
        current = options[0]
        set_preview_file(current)

    if st.session_state.get("preview_file_radio") not in options:
        st.session_state.preview_file_radio = current

    chosen = st.radio(
        "미리볼 파일",
        options=options,
        format_func=lambda file_id: labels[file_id],
        horizontal=True,
        key="preview_file_radio",
    )
    if chosen != st.session_state.get("preview_file_id"):
        set_preview_file(chosen)


def _switch_preview_sheet(file_id: str, meta: dict, sheet_name: str) -> None:
    path = meta.get("path")
    if not path:
        return

    df = load_excel(path, sheet_name=sheet_name)
    meta["current_sheet"] = sheet_name
    st.session_state.setdefault("file_frames", {})[file_id] = df

    sanitized_ids = st.session_state.setdefault("_preview_sanitized_ids", set())
    sanitized_ids.discard(file_id)

    # 미리보는 파일이 곧 분석 대상이면 분석용 상태도 맞춤
    if file_id == st.session_state.get("active_file_id"):
        st.session_state.df = df
        st.session_state.current_sheet = sheet_name
        st.session_state.sheet_names = meta.get("sheet_names") or []
        st.session_state._df_sanitized = True
        st.session_state.selected_df = None
        st.session_state.operation_result = None

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
