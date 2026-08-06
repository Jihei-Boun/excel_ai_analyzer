"""다중 파일 비교·병합 UI."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from core.io.export_utils import dataframe_to_xlsx_bytes, export_dataframe_xlsx, safe_download_name
from core.io.merge_engine import infer_common_keys, merge_named_frames
from core.io.normalize import align_column_names, column_match_key
from ui.file_state import (
    _ensure_file_frame,
    find_file,
)


def render_merge_section() -> None:
    files = st.session_state.get("uploaded_files") or []
    if len(files) < 2:
        return

    st.subheader("파일 병합")
    st.caption(
        "공통 키로 여러 엑셀을 합쳐 새 파일을 만듭니다. "
        "병합 전 매칭률·누락·충돌을 확인하세요."
    )

    labels = {meta["id"]: meta["name"] for meta in files}
    default_ids = list(st.session_state.get("active_file_ids") or [])
    if len(default_ids) < 2:
        default_ids = [meta["id"] for meta in files[:2]]

    selected_ids = st.multiselect(
        "병합할 파일",
        options=[meta["id"] for meta in files],
        default=[i for i in default_ids if i in labels],
        format_func=lambda file_id: labels.get(file_id, file_id),
        key="merge_file_ids",
    )
    if len(selected_ids) < 2:
        st.info("병합하려면 파일을 2개 이상 선택하세요.")
        return

    named_frames = _collect_named_frames(selected_ids)
    if len(named_frames) < 2:
        st.warning("선택한 파일의 데이터를 불러오지 못했습니다.")
        return

    aligned = align_column_names([frame for _, frame in named_frames])
    named_frames = [(name, frame) for (name, _), frame in zip(named_frames, aligned)]

    common_cols = _common_columns(named_frames)
    suggested = infer_common_keys(named_frames)
    key_options = common_cols or suggested
    if not key_options:
        st.error(
            "공통 컬럼이 없어 자동 병합할 수 없습니다. "
            "컬럼명을 맞춘 뒤 다시 시도하세요."
        )
        return

    default_keys = [k for k in suggested if k in key_options] or key_options[:1]
    keys = st.multiselect(
        "조인 키",
        options=key_options,
        default=default_keys,
        key="merge_keys",
        help="파일 간 같은 값을 갖는 컬럼을 선택하세요.",
    )
    how = st.radio(
        "조인 방식",
        options=["outer", "left", "inner"],
        format_func=lambda v: {
            "outer": "outer (전체 합치기)",
            "left": "left (첫 파일 기준)",
            "inner": "inner (공통만)",
        }[v],
        horizontal=True,
        key="merge_how",
    )

    if not keys:
        st.info("조인 키를 하나 이상 선택하세요.")
        return

    if st.button("병합 미리보기", type="primary", key="merge_preview_btn"):
        try:
            result = merge_named_frames(named_frames, keys=keys, how=how)  # type: ignore[arg-type]
        except ValueError as exc:
            st.error(str(exc))
            return
        st.session_state.merge_result_df = result.dataframe
        st.session_state.merge_result_report = result.report.to_dict()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            path = export_dataframe_xlsx(
                result.dataframe,
                filename=f"merged_{stamp}.xlsx",
            )
            st.session_state.merge_export_path = str(path)
        except Exception as exc:  # noqa: BLE001
            st.session_state.merge_export_path = None
            st.warning(f"디스크 저장은 실패했지만 다운로드는 가능합니다: {exc}")

    report = st.session_state.get("merge_result_report")
    merged_df = st.session_state.get("merge_result_df")
    if report and merged_df is not None:
        _render_merge_report(report)
        st.dataframe(merged_df.head(50), use_container_width=True, hide_index=True)
        if len(merged_df) > 50:
            st.caption(f"상위 50행만 표시합니다. (전체 {len(merged_df):,}행)")

        payload = dataframe_to_xlsx_bytes(merged_df)
        file_name = safe_download_name(
            f"merged_{'_'.join(keys)}_{how}",
            default="merged.xlsx",
        )
        st.download_button(
            "병합 결과 Excel 다운로드",
            data=payload,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="merge_download_btn",
        )
        export_path = st.session_state.get("merge_export_path")
        if export_path:
            st.caption(f"서버 저장 경로: `{export_path}`")


def _collect_named_frames(file_ids: list[str]) -> list[tuple[str, object]]:
    frames: list[tuple[str, object]] = []
    for file_id in file_ids:
        meta = find_file(file_id)
        if meta is None:
            continue
        sheet = meta.get("current_sheet") or (meta.get("sheet_names") or [None])[0]
        if not sheet:
            continue
        try:
            df = _ensure_file_frame(file_id, meta, sheet)
        except Exception:
            continue
        frames.append((meta["name"], df))
    return frames


def _common_columns(named_frames: list[tuple[str, object]]) -> list[str]:
    if not named_frames:
        return []
    sets = [{column_match_key(c) for c in frame.columns} for _, frame in named_frames]
    common = set.intersection(*sets) if sets else set()
    primary = named_frames[0][1]
    ordered = []
    seen = set()
    for col in primary.columns:
        key = column_match_key(col)
        if key in common and key not in seen:
            ordered.append(str(col))
            seen.add(key)
    return ordered


def _render_merge_report(report: dict) -> None:
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("결과 행", f"{report.get('result_rows', 0):,}")
    with c2:
        st.metric("매칭률", f"{float(report.get('match_rate', 0)):.1%}")
    with c3:
        st.metric("누락률", f"{float(report.get('missing_rate', 0)):.1%}")
    with c4:
        st.metric("입력 파일", len(report.get("input_names") or []))

    warnings = report.get("warnings") or []
    if warnings:
        for warning in warnings:
            st.warning(warning)
    notes = report.get("notes") or []
    for note in notes:
        st.info(note)
    samples = report.get("missing_key_samples") or []
    if samples:
        with st.expander("누락 키 샘플", expanded=False):
            for sample in samples:
                st.text(f"· {sample}")
