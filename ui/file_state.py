"""업로드 파일·분석 대상 session_state API (UI 제외)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.excel_loader import load_excel
from ui.session_store import clear_analysis_result_state, clear_selection_and_operation


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

    sheet = meta.get("current_sheet") or (meta.get("sheet_names") or [None])[0]
    if sheet:
        _ensure_file_frame(file_id, meta, sheet)

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

    sheet = meta.get("current_sheet") or (meta.get("sheet_names") or [None])[0]
    if not sheet:
        return preview_id, meta, None
    df = _ensure_file_frame(preview_id, meta, sheet)
    return preview_id, meta, df


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
    for file_id in ordered_ids:
        meta = find_file(file_id)
        if meta is None:
            continue
        _normalize_active_sheets(meta)
        sheet = meta.get("current_sheet") or meta["active_sheets"][0]
        _ensure_file_frame(file_id, meta, sheet)

    # 분석 대상의 대표 파일 (단일 모드 = 유일한 파일)
    primary_id = ordered_ids[0]
    primary_meta = find_file(primary_id)
    if primary_meta is None:
        return

    primary_df = st.session_state.setdefault("file_frames", {})[primary_id]
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
        clear_analysis_result_state(work_target=_work_target_label())


def is_multi_file_analysis() -> bool:
    """파일 2개 이상을 동시에 분석하는 모드."""
    return (
        st.session_state.get("analysis_mode") == "multi"
        and len(st.session_state.get("active_file_ids") or []) >= 2
    )


def is_multi_sheet_analysis() -> bool:
    """단일 파일에서 시트 2개 이상을 동시에 분석하는 모드."""
    if is_multi_file_analysis():
        return False
    active_ids = list(st.session_state.get("active_file_ids") or [])
    if len(active_ids) != 1:
        return False
    meta = find_file(active_ids[0])
    if meta is None:
        return False
    return len(_normalize_active_sheets(meta)) >= 2


def is_multi_analysis_mode() -> bool:
    """파일 또는 시트 단위로 2개 이상 동시 분석 중인지."""
    return is_multi_file_analysis() or is_multi_sheet_analysis()


def get_active_sheet_names() -> list[str]:
    """단일 파일 분석 시 선택된 시트 목록. 다중 파일이면 빈 목록."""
    if is_multi_file_analysis():
        return []
    active_id = st.session_state.get("active_file_id")
    meta = find_file(active_id) if active_id else None
    if meta is None:
        return []
    return list(_normalize_active_sheets(meta))


def set_active_sheets(
    sheet_names: list[str],
    *,
    file_id: str | None = None,
    reset_analysis: bool = True,
) -> None:
    """단일 파일의 분석 대상 시트를 설정한다. 2개 이상이면 시트 동시 분석."""
    target_id = file_id or st.session_state.get("active_file_id")
    meta = find_file(target_id) if target_id else None
    if meta is None or not target_id:
        return

    available = list(meta.get("sheet_names") or [])
    if not available:
        return

    ordered: list[str] = []
    for name in sheet_names:
        if name in available and name not in ordered:
            ordered.append(name)
    if not ordered:
        ordered = [meta.get("current_sheet") or available[0]]

    meta["active_sheets"] = ordered
    # 대표 시트(미리보기·단일 분석): 선택 목록의 첫 시트
    primary_sheet = ordered[0]
    meta["current_sheet"] = primary_sheet
    df = _ensure_file_frame(target_id, meta, primary_sheet)

    # 선택 시트 프레임을 미리 로드
    for sheet in ordered:
        _load_sheet_frame(target_id, meta, sheet)

    st.session_state.active_file_ids = [target_id]
    st.session_state.active_file_id = target_id
    st.session_state.analysis_mode = "single"
    st.session_state.df = df
    st.session_state.file_name = meta["name"]
    st.session_state.file_path = meta["path"]
    st.session_state.file_size = meta["size"]
    st.session_state.sheet_names = available
    st.session_state.current_sheet = primary_sheet
    st.session_state._df_sanitized = False

    if reset_analysis:
        clear_analysis_result_state()
    st.session_state.work_target = _work_target_label()
    # 사이드바 시트 multiselect는 이미 인스턴스화된 뒤일 수 있으므로
    # 다음 스크립트 시작(위젯 생성 전)에 동기화한다.
    st.session_state._pending_sidebar_sheets = {
        "file_id": target_id,
        "sheets": list(ordered),
    }
    queue_widget_sync(sync_mode_radio=False)


def get_active_named_frames() -> list[tuple[str, pd.DataFrame]]:
    """활성 분석 단위 순서대로 (표시명, DataFrame) 목록을 반환한다.

    - 다중 파일: 파일명 기준 (각 파일의 current_sheet)
    - 다중 시트: 시트명 기준
    """
    named: list[tuple[str, pd.DataFrame]] = []

    if is_multi_file_analysis():
        for file_id in st.session_state.get("active_file_ids") or []:
            meta = find_file(file_id)
            if meta is None:
                continue
            sheet = meta.get("current_sheet") or (meta.get("sheet_names") or [None])[0]
            if not sheet:
                continue
            df = _ensure_file_frame(file_id, meta, sheet)
            named.append((meta["name"], df))
        return named

    active_id = st.session_state.get("active_file_id")
    meta = find_file(active_id) if active_id else None
    if meta is None or not active_id:
        return named

    sheets = _normalize_active_sheets(meta)
    if len(sheets) >= 2:
        for sheet in sheets:
            df = _load_sheet_frame(active_id, meta, sheet)
            named.append((sheet, df))
        return named

    sheet = sheets[0] if sheets else meta.get("current_sheet")
    if not sheet:
        return named
    df = _ensure_file_frame(active_id, meta, sheet)
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

    pending_sheets = st.session_state.pop("_pending_sidebar_sheets", None)
    if isinstance(pending_sheets, dict):
        file_id = pending_sheets.get("file_id")
        sheets = pending_sheets.get("sheets")
        if file_id and isinstance(sheets, list):
            st.session_state[f"sidebar_sheets_{file_id}"] = list(sheets)


def current_analysis_mode() -> str:
    """파일 범위 라디오용. 시트 동시 분석은 단일 파일로 본다."""
    return "multi" if is_multi_file_analysis() else "single"


def remove_file(file_id: str) -> None:
    """업로드 목록에서 파일을 제거하고, 필요하면 다른 파일을 활성화한다."""
    files = [f for f in st.session_state.uploaded_files if f["id"] != file_id]
    st.session_state.uploaded_files = files

    frames = st.session_state.setdefault("file_frames", {})
    frames.pop(file_id, None)
    _drop_sheet_frames_for_file(file_id)

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
            if is_multi_file_analysis() or len(files) >= 2:
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


def find_file(file_id: str) -> dict | None:
    for meta in st.session_state.get("uploaded_files") or []:
        if meta["id"] == file_id:
            return meta
    return None


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
    clear_selection_and_operation()
    st.session_state.work_target = "원본 df"
    st.session_state._df_sanitized = False
    st.session_state._preview_sanitized_ids = set()
    st.session_state.sheet_frames = {}


def _work_target_label() -> str:
    if is_multi_file_analysis():
        return "다중 파일"
    if is_multi_sheet_analysis():
        return "다중 시트"
    return "원본 df"


def _normalize_active_sheets(meta: dict) -> list[str]:
    """meta.active_sheets를 유효한 시트 목록으로 맞춘다."""
    available = list(meta.get("sheet_names") or [])
    if not available:
        meta["active_sheets"] = []
        return []

    current = meta.get("active_sheets")
    if not current:
        fallback = meta.get("current_sheet") or available[0]
        meta["active_sheets"] = [fallback]
        return list(meta["active_sheets"])

    ordered = [name for name in current if name in available]
    if not ordered:
        ordered = [meta.get("current_sheet") or available[0]]
    meta["active_sheets"] = ordered
    return ordered


def _sheet_frame_key(file_id: str, sheet_name: str) -> str:
    return f"{file_id}::{sheet_name}"


def _load_sheet_frame(file_id: str, meta: dict, sheet_name: str) -> pd.DataFrame:
    cache = st.session_state.setdefault("sheet_frames", {})
    key = _sheet_frame_key(file_id, sheet_name)
    if key not in cache:
        cache[key] = load_excel(meta["path"], sheet_name=sheet_name)
    return cache[key]


def _ensure_file_frame(file_id: str, meta: dict, sheet_name: str) -> pd.DataFrame:
    """미리보기·단일 분석용 file_frames[file_id]를 해당 시트로 맞춘다."""
    df = _load_sheet_frame(file_id, meta, sheet_name)
    st.session_state.setdefault("file_frames", {})[file_id] = df
    return df


def _drop_sheet_frames_for_file(file_id: str) -> None:
    cache = st.session_state.get("sheet_frames") or {}
    prefix = f"{file_id}::"
    for key in [k for k in cache if k.startswith(prefix)]:
        cache.pop(key, None)


def _format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"
