"""데이터 업로드 영역 — 다중 엑셀 지원."""

from __future__ import annotations

from collections import Counter

import pandas as pd
import streamlit as st

from core.constants import UPLOAD_DIR
from core.io.excel_loader import CSV_SHEET_NAME, is_csv_path, load_tabular
from core.schema.quality import friendly_load_error
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
        on_change=_mark_uploader_changed,
    )
    # 업로더 칩(X)으로 뺀 파일은 session_state에서도 뺀다. 마지막 파일을 X로 빼서 목록이
    # 비었을 때도 포함한다. 같은 실행에서 새로 올린 파일은 그대로 처리한다.
    # 사용자가 업로더를 실제로 바꾼 실행에서만 맞춘다. 다른 위젯이 업로더를 그리기 전에
    # rerun하면 위젯 값이 잠깐 빈 목록으로 오는데, 그때 파일을 지우면 안 된다.
    # 지금 위젯에 같은 이름이 몇 개 있는지. 삭제 버튼이 숨길 칩 수를 여기서 읽는다.
    st.session_state._uploader_name_counts = dict(Counter(u.name for u in uploaded_list or []))
    removed = False
    if st.session_state.pop("_uploader_user_changed", False):
        removed = _prune_files_removed_in_uploader(uploaded_list or [])
    if uploaded_list:
        _handle_uploads(uploaded_list)  # 새 파일이 있으면 여기서 rerun
    if removed:
        st.rerun()

    _render_file_list()


def _mark_uploader_changed() -> None:
    """사용자가 업로더에 파일을 올리거나 칩(X)으로 뺐을 때만 Streamlit이 부른다."""
    st.session_state._uploader_user_changed = True


def files_removed_in_uploader(current_names: set[str], session_ids: list[str]) -> list[str]:
    """사용자가 바꾼 업로더에 더 이상 없는데 session에는 남은 파일."""
    return [file_id for file_id in session_ids if file_id not in current_names]


def visible_uploads(uploaded_list: list, hidden: dict[str, int]) -> list:
    """삭제 버튼으로 숨긴 칩(이름마다 앞에서부터 hidden[이름]개)을 뺀 업로드.

    같은 이름을 삭제 버튼으로 지운 뒤 다시 올리면 위젯에는 숨긴 칩과 새 칩이 함께 있다.
    숨긴 개수만큼만 건너뛰므로 새 칩은 보이고 다시 읽힌다.
    """
    seen: Counter = Counter()
    out = []
    for uploaded in uploaded_list:
        seen[uploaded.name] += 1
        if seen[uploaded.name] > hidden.get(uploaded.name, 0):
            out.append(uploaded)
    return out


def _hidden_counts() -> dict[str, int]:
    hidden = st.session_state.get("_uploader_hidden_counts")
    if not isinstance(hidden, dict):
        hidden = {}
        st.session_state._uploader_hidden_counts = hidden
    return hidden


def _prune_files_removed_in_uploader(uploaded_list) -> bool:
    """업로더 칩(X)으로 빠진 파일을 session_state에서도 제거한다. 변경이 있으면 True."""
    hidden = _hidden_counts()
    counts = Counter(uploaded.name for uploaded in uploaded_list)
    # 숨긴 칩보다 위젯의 칩이 적어졌으면(사용자가 X로 뺐으면) 숨김 수도 줄인다.
    for name in list(hidden):
        hidden[name] = min(hidden[name], counts.get(name, 0))
        if not hidden[name]:
            del hidden[name]
    current = {uploaded.name for uploaded in visible_uploads(uploaded_list, hidden)}
    session_ids = [meta["id"] for meta in st.session_state.get("uploaded_files") or []]
    removed = files_removed_in_uploader(current, session_ids)
    for file_id in removed:
        # 위젯에서 이미 빠졌으므로 위젯을 초기화하거나 칩을 숨길 필요가 없다.
        # (초기화하면 같은 실행에 새로 올린 파일까지 사라진다.)
        remove_file(file_id, from_uploader=True)
    return bool(removed)


def _handle_uploads(uploaded_list) -> None:
    existing_ids = {f["id"] for f in st.session_state.uploaded_files}
    new_ids: list[str] = []

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # 삭제 버튼으로 지운 파일의 칩은 위젯에 남아 있지만 숨겨 두었으므로 다시 읽지 않는다.
    for uploaded in visible_uploads(uploaded_list, _hidden_counts()):
        file_id = uploaded.name
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
