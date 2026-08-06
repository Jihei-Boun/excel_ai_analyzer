"""데이터 미리보기 — 분석 대상과 무관하게 업로드된 파일을 탐색한다."""

from __future__ import annotations

import streamlit as st

from core.excel_loader import load_tabular, sanitize_dataframe
from core.quality import diagnose_dataframe, format_quality_summary, friendly_load_error
from ui.display import for_preview_display, preview_column_labels, render_dataframe
from ui.file_state import (
    get_preview_context,
    preserves_active_sheets_on_preview,
    set_preview_file,
)
from ui.session_store import clear_selection_and_operation


def render_preview_section() -> None:
    files = st.session_state.get("uploaded_files") or []
    if not files:
        st.info("파일을 업로드하면 미리보기가 표시됩니다.")
        return

    _render_preview_file_picker(files)

    preview_id, meta, df = get_preview_context()
    if preview_id is None or meta is None or df is None:
        st.info("미리볼 파일을 선택하세요.")
        return

    st.caption(f"미리보기 · {meta['name']}")

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
        label = "미리볼 시트"
        if (
            preview_id in (st.session_state.get("active_file_ids") or [])
            and preserves_active_sheets_on_preview(preview_id)
        ):
            label = "미리볼 시트 (분석 시트와 별개)"
        options = list(sheet_names)
        default = current_sheet if current_sheet in options else options[0]
        widget_key = f"preview_sheet_{preview_id}"
        if st.session_state.get(widget_key) not in options:
            st.session_state[widget_key] = default
        selected = st.radio(
            label,
            options=options,
            horizontal=True,
            key=widget_key,
        )
        if selected != current_sheet:
            _switch_preview_sheet(preview_id, meta, selected)
            return

    st.subheader("데이터 미리보기")
    height = min(900, max(280, 38 * (min(len(df), 100) + 1)))
    display_df = for_preview_display(df.head(100) if len(df) > 100 else df)
    column_labels = preview_column_labels(list(display_df.columns))
    column_config = {
        column: st.column_config.Column(label=label)
        for column, label in column_labels.items()
    }
    render_dataframe(
        display_df,
        height=height,
        hide_index=True,
        column_config=column_config,
        column_labels=column_labels,
    )
    if len(df) > 100:
        st.caption(f"상위 100행만 표시합니다. (전체 {len(df):,}행)")
    _render_summary_cards(df)
    _render_quality_panel(df, label=meta["name"])


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

    try:
        df = load_tabular(path, sheet_name=sheet_name)
    except Exception as exc:  # noqa: BLE001
        st.error(friendly_load_error(exc, path=str(path)))
        return
    meta["current_sheet"] = sheet_name
    st.session_state.setdefault("file_frames", {})[file_id] = df
    st.session_state.setdefault("sheet_frames", {})[f"{file_id}::{sheet_name}"] = df

    sanitized_ids = st.session_state.setdefault("_preview_sanitized_ids", set())
    sanitized_ids.discard(file_id)

    # 미리보는 파일이 곧 분석 대상이면…
    if file_id in (st.session_state.get("active_file_ids") or []):
        if file_id == st.session_state.get("active_file_id"):
            st.session_state.sheet_names = meta.get("sheet_names") or []
        # 시트 동시 분석 중이면 미리보기만 바꾸고 분석 선택 시트는 유지
        if preserves_active_sheets_on_preview(file_id):
            st.rerun()
            return
        # 단일 시트 분석: 미리보기 시트 = 분석 시트
        meta["active_sheets"] = [sheet_name]
        if file_id == st.session_state.get("active_file_id"):
            st.session_state.df = df
            st.session_state.current_sheet = sheet_name
            st.session_state._df_sanitized = True
            clear_selection_and_operation()
        # 사이드바 시트 multiselect는 위젯 생성 전에 맞춘다
        st.session_state._pending_sidebar_sheets = {
            "file_id": file_id,
            "sheets": [sheet_name],
        }

    st.rerun()


def _render_summary_cards(df) -> None:
    numeric_cols = df.select_dtypes(include="number").shape[1]
    string_cols = df.select_dtypes(include=["object", "string"]).shape[1]
    missing = int(df.isna().sum().sum())
    completeness = (1 - missing / max(df.size, 1)) * 100

    with st.container(border=True):
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.metric("총 행", f"{len(df):,}")
        with c2:
            st.metric("총 열", len(df.columns))
        with c3:
            st.metric("수치형 컬럼", numeric_cols)
        with c4:
            st.metric("문자형 컬럼", string_cols)
        with c5:
            st.metric("데이터 완전성", f"{completeness:.1f}%")


def _render_quality_panel(df, *, label: str) -> None:
    report = diagnose_dataframe(df, label=label)
    st.caption(format_quality_summary(report, label=label))

    if report.severity == "ok" and not report.warnings:
        return

    if report.severity == "error":
        for warning in report.warnings:
            st.error(warning)
    else:
        for warning in report.warnings:
            st.warning(warning)

    if report.suggestions or report.suspected_key_columns:
        with st.expander("데이터 품질 가이드", expanded=report.severity != "ok"):
            if report.suspected_key_columns:
                st.markdown(
                    "**키 후보:** "
                    + ", ".join(f"`{c}`" for c in report.suspected_key_columns)
                )
            for tip in report.suggestions:
                st.markdown(f"- {tip}")
