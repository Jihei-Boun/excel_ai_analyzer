"""데이터 업로드 영역 — 다중 엑셀 지원."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.constants import UPLOAD_DIR
from core.excel_loader import CSV_SHEET_NAME, is_csv_path, load_tabular
from core.quality import friendly_load_error
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
    get_analysis_unit_label,
    get_preview_context,
    is_cross_file_sheet_analysis,
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
    "is_cross_file_sheet_analysis",
    "is_multi_analysis_mode",
    "get_analysis_unit_label",
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
    st.subheader("데이터")
    st.caption(
        "엑셀·CSV를 여러 개 올릴 수 있습니다. "
        "분석할 파일은 왼쪽 사이드바에서, 미리보기는 아래에서 선택하세요."
    )

    uploaded_list = st.file_uploader(
        "엑셀 또는 CSV 파일을 업로드하세요.",
        type=["xlsx", "xls", "csv"],
        accept_multiple_files=True,
        key=f"excel_uploader_{st.session_state.get('uploader_nonce', 0)}",
    )
    # 업로더에서 X로 제거된 파일을 session_state와 맞춤 (빈 목록일 때는 건드리지 않음)
    if uploaded_list and _prune_files_removed_in_uploader(uploaded_list):
        st.rerun()
    if uploaded_list:
        _handle_uploads(uploaded_list)

    _render_file_list()


def _prune_files_removed_in_uploader(uploaded_list) -> bool:
    """업로더 칩(X)으로 빠진 파일을 session_state에서도 제거한다.

    삭제 버튼 경로와 달리 위젯 value에 파일이 더 이상 없을 때 호출된다.
    변경이 있으면 True.
    """
    widget_names = {uploaded.name for uploaded in uploaded_list}
    changed = False
    for meta in list(st.session_state.get("uploaded_files") or []):
        if meta["id"] not in widget_names:
            remove_file(meta["id"])
            changed = True
    return changed


def _handle_uploads(uploaded_list) -> None:
    existing_ids = {f["id"] for f in st.session_state.uploaded_files}
    excluded = st.session_state.setdefault("_uploader_excluded_names", set())
    new_ids: list[str] = []

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    for uploaded in uploaded_list:
        file_id = uploaded.name
        # 삭제 버튼으로 제거된 파일: 위젯에 칩이 남아 있어도 session에 다시 넣지 않음
        if file_id in excluded:
            continue
        if file_id in existing_ids:
            continue

        save_path = UPLOAD_DIR / uploaded.name
        save_path.write_bytes(uploaded.getbuffer())

        try:
            if is_csv_path(save_path):
                sheet_names = [CSV_SHEET_NAME]
                current_sheet = CSV_SHEET_NAME
                df = load_tabular(save_path)
            else:
                excel = pd.ExcelFile(save_path)
                sheet_names = excel.sheet_names
                if not sheet_names:
                    raise ValueError("시트가 없는 엑셀 파일입니다.")
                current_sheet = sheet_names[0]
                df = load_tabular(save_path, sheet_name=current_sheet)
        except Exception as exc:  # noqa: BLE001
            st.error(friendly_load_error(exc, path=uploaded.name))
            continue

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
        # 새로 올린 이름은 제외 목록에서 제거
        for file_id in new_ids:
            excluded.discard(file_id)
        st.session_state.preview_file_id = new_ids[-1]
        st.session_state._pending_preview_radio = new_ids[-1]
        _maybe_apply_suggested_profile(new_ids[-1])
        if len(st.session_state.get("uploaded_files") or []) >= 2:
            all_ids = [meta["id"] for meta in st.session_state.uploaded_files]
            activate_files(all_ids, reset_analysis=True)
        else:
            activate_file(new_ids[-1], reset_analysis=True)
        st.rerun()


def _maybe_apply_suggested_profile(file_id: str) -> None:
    """사용자가 프로필을 수동 고정하지 않았다면 업로드 표로 자동 추천한다.

    selectbox(key=analysis_profile) 이후에는 같은 키를 직접 수정할 수 없으므로
    pending으로 넘기고 다음 런의 apply_pending_widget_sync에서 적용한다.
    """
    if st.session_state.get("profile_manually_set"):
        return
    from core.profile_loader import suggest_profile_name

    frames = st.session_state.get("file_frames") or {}
    df = frames.get(file_id)
    if df is None:
        return
    suggested, score = suggest_profile_name(df)
    st.session_state.suggested_profile = suggested
    st.session_state.suggested_profile_score = score
    st.session_state._pending_analysis_profile = suggested
    st.session_state.budget_table_mode = suggested == "budget"


def _render_file_list() -> None:
    """업로드 목록 + 삭제. 칩은 업로더 안에, 삭제는 여기서 처리."""
    files = st.session_state.get("uploaded_files") or []
    if not files:
        return

    multi_mode = is_multi_file_analysis()
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
            if is_analysis and len(sheets) >= 2:
                sheet_note = f" · 시트 {len(sheets)}개"
            st.caption(
                f'{mark}{meta["name"]} · {meta["size"]}{suffix}{sheet_note}'
            )
        with cols[1]:
            if st.button("삭제", key=f"del_{file_id}", use_container_width=True):
                remove_file(file_id)
                st.rerun()
