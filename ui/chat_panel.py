"""우측 AI 분석 채팅 패널 — Streamlit 네이티브 채팅/표 컴포넌트."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from core.constants import CHAT_EXAMPLE_LIMIT, CHAT_PREVIEW_ROWS
from core.suggest_prompts import suggest_example_prompts
from ui.chat import process_user_prompt
from ui.display import render_dataframe
from ui.upload import (
    get_active_named_frames,
    get_analysis_df,
    get_analysis_file_name,
    get_analysis_unit_label,
    is_cross_file_sheet_analysis,
    is_multi_analysis_mode,
    is_multi_file_analysis,
    is_multi_sheet_analysis,
)


def render_chat_panel() -> None:
    st.subheader("AI 분석")
    multi_mode = is_multi_analysis_mode()
    if multi_mode:
        active_names = [name for name, _ in get_active_named_frames()]
        unit = get_analysis_unit_label()
        st.caption(
            f"선택된 {len(active_names)}개 {unit}를 동시에 분석합니다. "
            "비교·병합·교차 집계를 자연어로 요청하세요."
        )
        st.caption(" · ".join(active_names))
    else:
        st.caption("현재 데이터에 원하는 분석을 자연어로 요청하세요.")

    _render_analysis_mode_setting()
    _render_chat_history()
    _process_pending_analysis()
    _render_selected_result()
    _render_operation_result()
    _render_chat_input()


def _render_analysis_mode_setting() -> None:
    files = st.session_state.get("uploaded_files") or []
    if len(files) < 2:
        return

    from ui.upload import current_analysis_mode, set_analysis_mode

    desired = current_analysis_mode()
    if "chat_analysis_mode_radio" not in st.session_state:
        st.session_state.chat_analysis_mode_radio = desired

    with st.expander("분석 설정", expanded=desired == "multi"):
        mode = st.radio(
            "분석 범위",
            options=["single", "multi"],
            format_func=lambda value: (
                "단일 파일 — 선택한 하나만 분석"
                if value == "single"
                else "동시 분석 — 선택한 여러 파일을 함께 분석"
            ),
            horizontal=False,
            key="chat_analysis_mode_radio",
        )
        if mode == "single":
            active_name = get_analysis_file_name() or "선택된 파일"
            st.caption(f"현재 분석 대상: {active_name} (사이드바에서 선택)")
        else:
            active_count = len(get_active_named_frames())
            unit = get_analysis_unit_label()
            if active_count >= 2:
                st.caption(f"현재 {active_count}개 {unit}가 동시 분석 대상입니다.")
            else:
                st.caption("왼쪽 사이드바에서 2개 이상 파일을 선택하세요.")

        if mode != desired:
            # 라디오 위젯이 이미 새 값을 갖고 있으므로 키를 다시 쓰지 않는다.
            set_analysis_mode(mode, sync_mode_radio=False)
            st.rerun()


def _example_prompts() -> list[str]:
    """모드·업로드 데이터에 맞춰 채팅 예시 질문을 고른다."""
    profile_name = str(st.session_state.get("analysis_profile") or "").strip().lower()
    if not profile_name:
        profile_name = (
            "budget" if st.session_state.get("budget_table_mode") else "generic"
        )
    multi_sheet = is_multi_sheet_analysis() or is_cross_file_sheet_analysis()
    multi_file = is_multi_file_analysis()
    df = get_analysis_df()
    return suggest_example_prompts(
        df,
        profile_name=profile_name,
        multi_file=multi_file,
        multi_sheet=multi_sheet,
        limit=CHAT_EXAMPLE_LIMIT,
    )


def _render_chat_history() -> None:
    messages = st.session_state.chat_messages
    if not messages:
        st.caption("아직 대화가 없습니다. 아래 예시로 시작해 보세요.")
        examples = _example_prompts()
        cols = st.columns(2)
        for idx, prompt in enumerate(examples[:CHAT_EXAMPLE_LIMIT]):
            with cols[idx % 2]:
                if st.button(prompt, key=f"ex_{idx}", use_container_width=True):
                    st.session_state.pending_prompt = prompt
                    st.rerun()
        return

    show_code = bool(st.session_state.get("show_analysis_code"))
    for message in messages:
        role = message["role"]
        avatar = "user" if role == "user" else "assistant"
        with st.chat_message(avatar):
            st.markdown(str(message.get("content") or ""))

            if message.get("filter_summary"):
                st.success(f"✓ {message['filter_summary']}")
            if message.get("filter_note"):
                st.info(f"↻ {message['filter_note']}")

            chart_path = message.get("chart_path")
            if chart_path:
                path = Path(str(chart_path))
                if path.is_file():
                    st.image(str(path), use_container_width=True)
                else:
                    st.caption(f"차트 파일을 찾을 수 없습니다: {path.name}")

            attached = message.get("dataframe")
            list_values = message.get("list_values")
            list_groups = message.get("list_groups")
            if list_values:
                _render_list_result(
                    list_values,
                    message.get("list_label"),
                    attached if isinstance(attached, pd.DataFrame) else None,
                    groups=list_groups if isinstance(list_groups, dict) else None,
                )
            elif isinstance(attached, pd.DataFrame) and not attached.empty:
                height = min(520, max(120, 38 * (min(len(attached), 15) + 1)))
                render_dataframe(attached, hide_index=True, height=height)

            workbook_bytes = message.get("workbook_bytes")
            if workbook_bytes:
                sheets = message.get("workbook_sheets") or []
                label = "통합 Excel 다운로드"
                if sheets:
                    label = f"통합 Excel 다운로드 ({', '.join(str(s) for s in sheets)})"
                st.download_button(
                    label,
                    data=workbook_bytes,
                    file_name="integrated_result.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_wb_{id(message)}",
                    use_container_width=True,
                )

            code = message.get("code")
            if show_code and code:
                with st.expander("실행 코드", expanded=False):
                    st.code(code, language="python")


def _render_list_result(
    values: list[str],
    label: str | None,
    full_df: pd.DataFrame | None,
    *,
    groups: dict[str, list[str]] | None = None,
) -> None:
    """단일·분류별 리스트 요청 결과를 불릿 목록으로 표시한다."""
    if groups:
        total = sum(len(items) for items in groups.values())
        title = (
            f"{label} · {total}개 · {len(groups)}개 분류"
            if label
            else f"{total}개 항목 · {len(groups)}개 분류"
        )
        st.caption(title)
        for group_name, items in groups.items():
            st.markdown(f"**{group_name}**")
            st.markdown("\n".join(f"- {item}" for item in items))
    else:
        title = f"{label} · {len(values)}개" if label else f"{len(values)}개 항목"
        st.caption(title)
        st.markdown("\n".join(f"- {value}" for value in values))

    if full_df is not None and not full_df.empty and len(full_df.columns) > 1:
        with st.expander("전체 데이터 보기", expanded=False):
            height = min(360, max(120, 38 * (min(len(full_df), 12) + 1)))
            render_dataframe(full_df, hide_index=True, height=height)


def _process_pending_analysis() -> None:
    prompt = st.session_state.pop("pending_analysis_prompt", "") or ""
    if prompt:
        process_user_prompt(prompt, user_already_added=True)


def _render_selected_result() -> None:
    """현재 선택(필터) 데이터. 채팅 마지막 메시지에 이미 같은 표가 있으면 생략."""
    selected = st.session_state.get("selected_df")
    filter_df = st.session_state.get("analysis_filter_df")
    # 집계 후에도 필터가 있으면 그걸 '선택 데이터'로 표시
    display_df = filter_df if filter_df is not None and len(filter_df) > 0 else selected
    if display_df is None or len(display_df) == 0:
        return

    messages = st.session_state.get("chat_messages") or []
    if messages:
        last = messages[-1]
        attached = last.get("dataframe") if last.get("role") == "assistant" else None
        if isinstance(attached, pd.DataFrame) and _same_frame(attached, display_df):
            return
        # 마지막이 집계 요약 표이면 선택 데이터는 아래에 한 번만 안내
        if isinstance(attached, pd.DataFrame) and not _same_frame(attached, display_df):
            st.caption(f"선택 데이터(필터 유지) · {len(display_df):,}행")
            height = min(360, max(120, 38 * (min(len(display_df), 10) + 1)))
            render_dataframe(display_df.head(CHAT_PREVIEW_ROWS), hide_index=True, height=height)
            return

    st.caption(f"표 결과 · {len(display_df):,}행")
    height = min(560, max(140, 38 * (min(len(display_df), 18) + 1)))
    render_dataframe(display_df, hide_index=True, height=height)


def _same_frame(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    if left is right:
        return True
    if left.shape != right.shape:
        return False
    try:
        return left.reset_index(drop=True).equals(right.reset_index(drop=True))
    except Exception:
        return False


def _format_metric_number(value: object) -> str | None:
    """짧은 숫자만 metric 표시용 문자열로 만든다. 그 외는 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return None
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return None


def _render_operation_result() -> None:
    result = st.session_state.get("operation_result")
    if result is None:
        return

    label = st.session_state.get("active_operation", "result")
    if isinstance(result, pd.DataFrame):
        st.caption(f"연산 결과 · {label}")
        render_dataframe(
            result,
            hide_index=True,
            height=min(220, max(100, 38 * (len(result) + 1))),
        )
        export_df = result
    else:
        # st.metric은 짧은 숫자용 — 긴 문자열은 잘리고 글씨가 커진다.
        numeric = _format_metric_number(result)
        if numeric is not None:
            st.metric(f"결과 · {label}", numeric)
        else:
            text = str(result).strip()
            if text:
                st.caption(f"결과 · {label}")
                st.markdown(text)
        export_df = st.session_state.get("analysis_filter_df")
        if export_df is None:
            export_df = st.session_state.get("selected_df")

    if export_df is not None and isinstance(export_df, pd.DataFrame):
        from core.export_utils import dataframe_to_xlsx_bytes

        st.download_button(
            "Excel 다운로드",
            data=dataframe_to_xlsx_bytes(export_df),
            file_name="analysis_result.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


def _render_chat_input() -> None:
    st.divider()
    default = st.session_state.pop("pending_prompt", "") or ""
    nonce = st.session_state.get("chat_input_nonce", 0)
    if default:
        nonce += 1
        st.session_state.chat_input_nonce = nonce

    # 기존 form + text_area + 전송 버튼 유지
    # (pending_prompt / pending_analysis_prompt / nonce 세션 로직 보존)
    with st.form("chat_prompt_form", clear_on_submit=True):
        prompt = st.text_area(
            "질문 입력",
            value=default,
            placeholder="예: 매출이 높은 상위 10개 행을 보여줘",
            label_visibility="collapsed",
            height=72,
            key=f"chat_prompt_input_{nonce}",
        )
        submitted = st.form_submit_button(
            "전송",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        if not prompt.strip():
            return
        if is_multi_analysis_mode():
            if len(get_active_named_frames()) < 2:
                unit = get_analysis_unit_label()
                st.warning(f"동시 분석 모드에서는 {unit} 2개 이상을 선택하세요.")
                return
        elif get_analysis_df() is None:
            st.warning("먼저 엑셀 파일을 업로드하세요.")
            return
        prompt = prompt.strip()
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        st.session_state.pending_analysis_prompt = prompt
        st.rerun()
