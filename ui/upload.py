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


def activate_file(
    file_id: str,
    *,
    reset_analysis: bool = True,
    sync_mode_radio: bool | None = None,
) -> None:
    """업로드된 파일 중 하나를 분석 대상으로 만든다."""
    activate_files(
        [file_id],
        reset_analysis=reset_analysis,
        sync_mode_radio=sync_mode_radio,
    )


def set_preview_file(file_id: str) -> None:
    """미리보기 파일만 전환한다. 분석 대상(active_file_*)은 바꾸지 않는다."""
    meta = find_file(file_id)
    if meta is None:
        return

    frames = st.session_state.setdefault("file_frames", {})
    if file_id not in frames:
        frames[file_id] = load_excel(meta["path"], sheet_name=meta["current_sheet"])

    st.session_state.preview_file_id = file_id
    st.session_state._pending_preview_radio = file_id


def get_analysis_df() -> pd.DataFrame | None:
    """단일 분석용 DataFrame. active_file_id 기준."""
    active_id = st.session_state.get("active_file_id")
    frames = st.session_state.get("file_frames") or {}
    if active_id and active_id in frames:
        return frames[active_id]
    return st.session_state.get("df")


def get_analysis_file_name() -> str | None:
    active_id = st.session_state.get("active_file_id")
    meta = find_file(active_id) if active_id else None
    if meta:
        return meta["name"]
    return st.session_state.get("file_name")


def get_preview_context() -> tuple[str | None, dict | None, pd.DataFrame | None]:
    """(preview_file_id, meta, df) — 분석 대상과 무관하게 미리보기용."""
    files = st.session_state.get("uploaded_files") or []
    if not files:
        return None, None, None

    preview_id = st.session_state.get("preview_file_id")
    if preview_id is None or find_file(preview_id) is None:
        preview_id = st.session_state.get("active_file_id") or files[0]["id"]
        st.session_state.preview_file_id = preview_id

    meta = find_file(preview_id)
    if meta is None:
        return None, None, None

    frames = st.session_state.setdefault("file_frames", {})
    if preview_id not in frames:
        frames[preview_id] = load_excel(meta["path"], sheet_name=meta["current_sheet"])

    return preview_id, meta, frames[preview_id]


def activate_files(
    file_ids: list[str],
    *,
    reset_analysis: bool = True,
    sync_mode_radio: bool | None = None,
) -> None:
    """단일 또는 다중 파일을 분석 대상으로 활성화한다.

    sync_mode_radio:
      True  — 모드 라디오 동기화 예약
      False — 예약하지 않음 (채팅 라디오가 이미 새 값일 때)
      None  — 모드가 바뀔 때만 예약
    """
    if not file_ids:
        _clear_active()
        return

    ordered_ids: list[str] = []
    for file_id in file_ids:
        if file_id not in ordered_ids and find_file(file_id) is not None:
            ordered_ids.append(file_id)
    if not ordered_ids:
        return

    prev_mode = current_analysis_mode()
    frames = st.session_state.setdefault("file_frames", {})
    for file_id in ordered_ids:
        meta = find_file(file_id)
        if meta is None:
            continue
        if file_id not in frames:
            frames[file_id] = load_excel(meta["path"], sheet_name=meta["current_sheet"])

    # 분석 대상의 대표 파일 (단일 모드 = 유일한 파일)
    primary_id = ordered_ids[0]
    primary_meta = find_file(primary_id)
    if primary_meta is None:
        return

    primary_df = frames[primary_id]
    st.session_state.active_file_ids = ordered_ids
    st.session_state.active_file_id = primary_id
    st.session_state.df = primary_df
    st.session_state.file_name = primary_meta["name"]
    st.session_state.file_path = primary_meta["path"]
    st.session_state.file_size = primary_meta["size"]
    st.session_state.sheet_names = primary_meta["sheet_names"]
    st.session_state.current_sheet = primary_meta["current_sheet"]
    st.session_state._df_sanitized = True

    # 미리보기는 분석 대상과 독립 — 없거나 삭제된 경우만 초기화
    preview_id = st.session_state.get("preview_file_id")
    if preview_id is None or find_file(preview_id) is None:
        st.session_state.preview_file_id = primary_id
        st.session_state._pending_preview_radio = primary_id

    if len(ordered_ids) >= 2:
        st.session_state.analysis_mode = "multi"
    else:
        st.session_state.analysis_mode = "single"

    new_mode = current_analysis_mode()
    should_sync_radio = (
        sync_mode_radio
        if sync_mode_radio is not None
        else prev_mode != new_mode
    )
    queue_widget_sync(sync_mode_radio=should_sync_radio)

    if reset_analysis:
        st.session_state.selected_df = None
        st.session_state.analysis_filter_df = None
        st.session_state.operation_result = None
        st.session_state.analysis_context_label = None
        st.session_state.last_filter_summary = ""
        st.session_state.work_target = (
            "다중 파일" if len(ordered_ids) >= 2 else "원본 df"
        )
        st.session_state.active_operation = None


def is_multi_analysis_mode() -> bool:
    return (
        st.session_state.get("analysis_mode") == "multi"
        and len(st.session_state.get("active_file_ids") or []) >= 2
    )


def get_active_named_frames() -> list[tuple[str, pd.DataFrame]]:
    """활성 파일 ID 순서대로 (파일명, DataFrame) 목록을 반환한다."""
    frames = st.session_state.setdefault("file_frames", {})
    named: list[tuple[str, pd.DataFrame]] = []
    for file_id in st.session_state.get("active_file_ids") or []:
        meta = find_file(file_id)
        df = frames.get(file_id)
        if meta is None or df is None:
            continue
        named.append((meta["name"], df))
    return named


def set_analysis_mode(mode: str, *, sync_mode_radio: bool = True) -> None:
    """분석 모드를 전환한다. multi 모드에서는 활성 파일이 2개 이상이어야 한다.

    sync_mode_radio=False: 채팅 라디오가 이미 새 값을 갖고 있을 때
    (위젯 생성 후 키를 다시 쓰지 않기 위함).
    """
    if mode not in {"single", "multi"}:
        return

    if mode == "multi":
        active_ids = list(st.session_state.get("active_file_ids") or [])
        if len(active_ids) < 2:
            all_ids = [meta["id"] for meta in st.session_state.get("uploaded_files") or []]
            if len(all_ids) >= 2:
                activate_files(
                    all_ids,
                    reset_analysis=True,
                    sync_mode_radio=sync_mode_radio,
                )
            else:
                st.session_state.analysis_mode = "single"
                queue_widget_sync(sync_mode_radio=sync_mode_radio)
                return
        else:
            st.session_state.analysis_mode = "multi"
            st.session_state.work_target = "다중 파일"
            st.session_state.selected_df = None
            st.session_state.operation_result = None
            st.session_state.active_operation = None
            queue_widget_sync(sync_mode_radio=sync_mode_radio)
        return

    # 단일 파일: 활성 파일을 정확히 1개로 고정
    st.session_state.analysis_mode = "single"
    active_id = st.session_state.get("active_file_id")
    active_ids = list(st.session_state.get("active_file_ids") or [])
    if active_id is None and active_ids:
        active_id = active_ids[0]
    if active_id is None:
        files = st.session_state.get("uploaded_files") or []
        if files:
            active_id = files[0]["id"]
    if active_id:
        activate_files(
            [active_id],
            reset_analysis=True,
            sync_mode_radio=sync_mode_radio,
        )
    else:
        queue_widget_sync(sync_mode_radio=sync_mode_radio)


def queue_widget_sync(*, sync_mode_radio: bool = True) -> None:
    """위젯 키 동기화를 다음 스크립트 시작 시점(위젯 생성 전)으로 미룬다."""
    st.session_state._pending_file_picker_sync = True
    if sync_mode_radio:
        st.session_state._pending_mode_radio_sync = True


def apply_pending_widget_sync() -> None:
    """app.py에서 사이드바/본문 위젯보다 먼저 호출한다."""
    mode = current_analysis_mode()
    if st.session_state.pop("_pending_mode_radio_sync", False):
        st.session_state.chat_analysis_mode_radio = mode

    if st.session_state.pop("_pending_file_picker_sync", False):
        active = st.session_state.get("active_file_id")
        if active:
            st.session_state.single_file_picker = active
            st.session_state.sidebar_single_file = active

    pending_preview = st.session_state.pop("_pending_preview_radio", None)
    if pending_preview is not None:
        st.session_state.preview_file_radio = pending_preview


def current_analysis_mode() -> str:
    return "multi" if is_multi_analysis_mode() else "single"


def remove_file(file_id: str) -> None:
    """업로드 목록에서 파일을 제거하고, 필요하면 다른 파일을 활성화한다."""
    files = [f for f in st.session_state.uploaded_files if f["id"] != file_id]
    st.session_state.uploaded_files = files

    frames = st.session_state.setdefault("file_frames", {})
    frames.pop(file_id, None)

    sanitized = st.session_state.get("_preview_sanitized_ids")
    if isinstance(sanitized, set):
        sanitized.discard(file_id)

    # 업로더 위젯을 리셋해 삭제 직후 동일 파일이 다시 추가되지 않게 함
    st.session_state.uploader_nonce = st.session_state.get("uploader_nonce", 0) + 1

    if st.session_state.get("preview_file_id") == file_id:
        st.session_state.preview_file_id = files[-1]["id"] if files else None
        if st.session_state.preview_file_id:
            st.session_state._pending_preview_radio = st.session_state.preview_file_id

    if st.session_state.get("active_file_id") == file_id or file_id in (
        st.session_state.get("active_file_ids") or []
    ):
        if files:
            if is_multi_analysis_mode() or len(files) >= 2:
                remaining = [
                    fid
                    for fid in (st.session_state.get("active_file_ids") or [])
                    if fid != file_id and any(f["id"] == fid for f in files)
                ]
                if len(remaining) >= 2:
                    activate_files(remaining, reset_analysis=True)
                else:
                    activate_file(files[-1]["id"], reset_analysis=True)
            else:
                activate_file(files[-1]["id"], reset_analysis=True)
        else:
            _clear_active()


def _clear_active() -> None:
    st.session_state.active_file_id = None
    st.session_state.active_file_ids = []
    st.session_state.preview_file_id = None
    st.session_state.analysis_mode = "single"
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
    st.session_state._preview_sanitized_ids = set()


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
        # 새 파일은 미리보기로도 열어 주고, 분석 대상은 기존 로직대로
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

    multi_mode = is_multi_analysis_mode()
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
            st.markdown(
                f'<p class="meta-line">{mark}{meta["name"]} · {meta["size"]}{suffix}</p>',
                unsafe_allow_html=True,
            )
        with cols[1]:
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
