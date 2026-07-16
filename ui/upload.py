"""데이터 업로드 영역 — 다중 엑셀 지원."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from core.excel_loader import load_excel

UPLOAD_DIR = Path(__file__).resolve().parents[1] / "data" / "uploads"


def render_upload_section() -> None:
    st.markdown('<p class="panel-title">데이터</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="panel-desc">엑셀을 여러 개 올릴 수 있습니다. 아래 목록에서 분석할 파일을 선택하세요.</p>',
        unsafe_allow_html=True,
    )

    uploaded_list = st.file_uploader(
        "엑셀 업로드",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
        label_visibility="collapsed",
        key=f"excel_uploader_{st.session_state.get('uploader_nonce', 0)}",
    )
    if uploaded_list:
        _handle_uploads(uploaded_list)

    _render_file_list()


def activate_file(file_id: str, *, reset_analysis: bool = True) -> None:
    """업로드된 파일 중 하나를 활성(미리보기·분석 대상)으로 만든다."""
    meta = find_file(file_id)
    if meta is None:
        return

    frames = st.session_state.setdefault("file_frames", {})
    df = frames.get(file_id)
    if df is None:
        df = load_excel(meta["path"], sheet_name=meta["current_sheet"])
        frames[file_id] = df

    st.session_state.active_file_id = file_id
    st.session_state.active_file_ids = [file_id]
    st.session_state.df = df
    st.session_state.file_name = meta["name"]
    st.session_state.file_path = meta["path"]
    st.session_state.file_size = meta["size"]
    st.session_state.sheet_names = meta["sheet_names"]
    st.session_state.current_sheet = meta["current_sheet"]
    st.session_state._df_sanitized = True

    if reset_analysis:
        st.session_state.selected_df = None
        st.session_state.operation_result = None
        st.session_state.work_target = "원본 df"
        st.session_state.active_operation = None


def remove_file(file_id: str) -> None:
    """업로드 목록에서 파일을 제거하고, 필요하면 다른 파일을 활성화한다."""
    files = [f for f in st.session_state.uploaded_files if f["id"] != file_id]
    st.session_state.uploaded_files = files

    frames = st.session_state.setdefault("file_frames", {})
    frames.pop(file_id, None)

    # 업로더 위젯을 리셋해 삭제 직후 동일 파일이 다시 추가되지 않게 함
    st.session_state.uploader_nonce = st.session_state.get("uploader_nonce", 0) + 1

    if st.session_state.get("active_file_id") == file_id:
        if files:
            activate_file(files[-1]["id"], reset_analysis=True)
        else:
            _clear_active()


def _clear_active() -> None:
    st.session_state.active_file_id = None
    st.session_state.active_file_ids = []
    st.session_state.df = None
    st.session_state.file_name = None
    st.session_state.file_path = None
    st.session_state.file_size = None
    st.session_state.sheet_names = []
    st.session_state.current_sheet = None
    st.session_state.selected_df = None
    st.session_state.operation_result = None
    st.session_state.work_target = "원본 df"
    st.session_state._df_sanitized = False


def _handle_uploads(uploaded_list) -> None:
    existing_ids = {f["id"] for f in st.session_state.uploaded_files}
    new_ids: list[str] = []

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    for uploaded in uploaded_list:
        file_id = uploaded.name
        if file_id in existing_ids:
            continue

        save_path = UPLOAD_DIR / uploaded.name
        save_path.write_bytes(uploaded.getbuffer())

        excel = pd.ExcelFile(save_path)
        sheet_names = excel.sheet_names
        current_sheet = sheet_names[0]
        df = load_excel(save_path, sheet_name=current_sheet)

        meta = {
            "id": file_id,
            "name": uploaded.name,
            "path": str(save_path),
            "size": _format_size(len(uploaded.getbuffer())),
            "sheet_names": sheet_names,
            "current_sheet": current_sheet,
        }

        files = [f for f in st.session_state.uploaded_files if f["id"] != file_id]
        files.append(meta)
        st.session_state.uploaded_files = files
        existing_ids.add(file_id)
        st.session_state.setdefault("file_frames", {})[file_id] = df
        new_ids.append(file_id)

    if new_ids:
        activate_file(new_ids[-1], reset_analysis=True)
        st.rerun()


def _render_file_list() -> None:
    files = st.session_state.get("uploaded_files") or []
    if not files:
        return

    active_id = st.session_state.get("active_file_id")
    st.caption(f"업로드된 파일 {len(files)}개")

    for meta in files:
        file_id = meta["id"]
        is_active = file_id == active_id
        cols = st.columns([5.5, 1.2, 1.2])
        with cols[0]:
            mark = "● " if is_active else "○ "
            label = f"{mark}{meta['name']} · {meta['size']}"
            if is_active:
                st.markdown(f'<p class="meta-line">{label} <em>(분석 중)</em></p>', unsafe_allow_html=True)
            else:
                st.markdown(f'<p class="meta-line">{label}</p>', unsafe_allow_html=True)
        with cols[1]:
            if st.button(
                "선택",
                key=f"pick_{file_id}",
                use_container_width=True,
                disabled=is_active,
            ):
                activate_file(file_id, reset_analysis=True)
                st.rerun()
        with cols[2]:
            if st.button("삭제", key=f"del_{file_id}", use_container_width=True):
                remove_file(file_id)
                st.rerun()


def find_file(file_id: str) -> dict | None:
    for meta in st.session_state.get("uploaded_files") or []:
        if meta["id"] == file_id:
            return meta
    return None


def _format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"
