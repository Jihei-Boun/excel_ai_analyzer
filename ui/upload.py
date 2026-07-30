"""데이터 업로드 영역 — 다중 엑셀 지원."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.constants import UPLOAD_DIR
from core.excel_loader import load_excel
from ui.file_state import (
    _ensure_file_frame,
    _format_size,
    _normalize_active_sheets,
    _sheet_frame_key,
    activate_file,
    activate_files,
    apply_pending_widget_sync,
    current_analysis_mode,
    find_file,
    get_active_named_frames,
    get_active_sheet_names,
    get_analysis_df,
    get_analysis_file_name,
    get_preview_context,
    is_multi_analysis_mode,
    is_multi_file_analysis,
    is_multi_sheet_analysis,
    queue_widget_sync,
    remove_file,
    set_active_sheets,
    set_analysis_mode,
    set_preview_file,
)

__all__ = [
    "render_upload_section",
    "activate_file",
    "activate_files",
    "set_preview_file",
    "get_analysis_df",
    "get_analysis_file_name",
    "get_preview_context",
    "is_multi_file_analysis",
    "is_multi_sheet_analysis",
    "is_multi_analysis_mode",
    "get_active_sheet_names",
    "set_active_sheets",
    "get_active_named_frames",
    "set_analysis_mode",
    "queue_widget_sync",
    "apply_pending_widget_sync",
    "current_analysis_mode",
    "remove_file",
    "find_file",
]


def render_upload_section() -> None:
    st.markdown('<p class="panel-title">데이터</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="panel-desc">엑셀을 여러 개 올릴 수 있습니다. '
        "분석할 파일은 왼쪽 사이드바에서, 미리보기는 아래에서 선택하세요.</p>",
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
            "active_sheets": [current_sheet],
        }

        files = [f for f in st.session_state.uploaded_files if f["id"] != file_id]
        files.append(meta)
        st.session_state.uploaded_files = files
        existing_ids.add(file_id)
        st.session_state.setdefault("file_frames", {})[file_id] = df
        st.session_state.setdefault("sheet_frames", {})[
            _sheet_frame_key(file_id, current_sheet)
        ] = df
        new_ids.append(file_id)

    if new_ids:
        # 새 파일은 미리보기로도 열어 두고, 분석 대상은 기존 로직대로
        st.session_state.preview_file_id = new_ids[-1]
        st.session_state._pending_preview_radio = new_ids[-1]
        if len(st.session_state.get("uploaded_files") or []) >= 2:
            all_ids = [meta["id"] for meta in st.session_state.uploaded_files]
            activate_files(all_ids, reset_analysis=True)
        else:
            activate_file(new_ids[-1], reset_analysis=True)
        st.rerun()


def _render_file_list() -> None:
    """업로드 목록만 표시. 분석 대상 선택은 사이드바에서 한다."""
    files = st.session_state.get("uploaded_files") or []
    if not files:
        return

    multi_mode = is_multi_file_analysis()
    # 단일 모드인데 활성 파일이 2개 이상이면 강제로 1개만 유지
    if not multi_mode and len(st.session_state.get("active_file_ids") or []) > 1:
        primary = st.session_state.get("active_file_id") or files[0]["id"]
        activate_file(primary, reset_analysis=False, sync_mode_radio=False)
        st.rerun()

    st.caption(f"업로드된 파일 {len(files)}개 · 분석 대상은 왼쪽 사이드바에서 선택")

    if len(files) >= 2:
        cols = st.columns([2, 1])
        with cols[0]:
            st.caption("동시 분석 모드" if multi_mode else "단일 파일 분석 모드")
        with cols[1]:
            if multi_mode:
                if st.button("단일 모드", key="mode_single", use_container_width=True):
                    set_analysis_mode("single")
                    st.rerun()
            else:
                if st.button("동시 분석", key="mode_multi", use_container_width=True):
                    set_analysis_mode("multi")
                    st.rerun()

    analysis_ids = set(st.session_state.get("active_file_ids") or [])
    for meta in files:
        file_id = meta["id"]
        is_analysis = file_id in analysis_ids
        cols = st.columns([6.5, 1.5])
        with cols[0]:
            mark = "● " if is_analysis else "○ "
            suffix = " · 분석 중" if is_analysis else ""
            sheets = _normalize_active_sheets(meta)
            sheet_note = ""
            if is_analysis and not multi_mode and len(sheets) >= 2:
                sheet_note = f" · 시트 {len(sheets)}개"
            st.markdown(
                f'<p class="meta-line">{mark}{meta["name"]} · {meta["size"]}'
                f"{suffix}{sheet_note}</p>",
                unsafe_allow_html=True,
            )
        with cols[1]:
            if st.button("삭제", key=f"del_{file_id}", use_container_width=True):
                remove_file(file_id)
                st.rerun()
