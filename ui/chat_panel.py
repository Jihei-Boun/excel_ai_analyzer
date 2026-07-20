"""우측 AI 분석 채팅 패널."""

from __future__ import annotations

import html
import io

import pandas as pd
import streamlit as st

from ui.chat import process_user_prompt
from ui.display import for_display
from ui.session_store import MULTI_FILE_PROMPTS, RECOMMENDED_PROMPTS
from ui.upload import (
    get_active_named_frames,
    get_analysis_df,
    get_analysis_file_name,
    is_multi_analysis_mode,
)


def render_chat_panel() -> None:
    st.markdown('<p class="panel-title">AI 분석</p>', unsafe_allow_html=True)
    multi_mode = is_multi_analysis_mode()
    if multi_mode:
        active_names = [name for name, _ in get_active_named_frames()]
        st.markdown(
            '<p class="panel-desc">'
            f"선택된 {len(active_names)}개 파일을 동시에 분석합니다. "
            "비교·병합·교차 집계를 자연어로 요청하세요."
            "</p>",
            unsafe_allow_html=True,
        )
        st.caption(" · ".join(active_names))
    else:
        st.markdown(
            '<p class="panel-desc">현재 데이터에 원하는 분석을 자연어로 요청하세요.</p>',
            unsafe_allow_html=True,
        )

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
            if active_count >= 2:
                st.caption(f"현재 {active_count}개 파일이 동시 분석 대상입니다.")
            else:
                st.caption("왼쪽 사이드바에서 2개 이상 파일을 선택하세요.")

        if mode != desired:
            # 라디오 위젯이 이미 새 값을 갖고 있으므로 키를 다시 쓰지 않는다.
            set_analysis_mode(mode, sync_mode_radio=False)
            st.rerun()


def _render_chat_history() -> None:
    messages = st.session_state.chat_messages
    if not messages:
        st.caption("아직 대화가 없습니다. 아래 예시로 시작해 보세요.")
        examples = MULTI_FILE_PROMPTS if is_multi_analysis_mode() else RECOMMENDED_PROMPTS
        cols = st.columns(2)
        for idx, prompt in enumerate(examples[:4]):
            with cols[idx % 2]:
                if st.button(prompt, key=f"ex_{idx}", use_container_width=True):
                    st.session_state.pending_prompt = prompt
                    st.rerun()
        return

    for message in messages:
        role = message["role"]
        css = "chat-user" if role == "user" else "chat-assistant"
        st.markdown(f'<div class="{css}">{message["content"]}</div>', unsafe_allow_html=True)

        if message.get("filter_summary"):
            st.markdown(
                f'<div class="filter-ok">✓ {message["filter_summary"]}</div>',
                unsafe_allow_html=True,
            )

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
            st.dataframe(
                for_display(attached),
                use_container_width=True,
                hide_index=True,
                height=height,
            )


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
        parts: list[str] = []
        for group_name, items in groups.items():
            parts.append(
                f'<div class="list-group-name">{html.escape(group_name)}</div>'
            )
            parts.append('<ul class="list-result list-result-nested">')
            parts.extend(f"<li>{html.escape(item)}</li>" for item in items)
            parts.append("</ul>")
        st.markdown("".join(parts), unsafe_allow_html=True)
    else:
        title = f"{label} · {len(values)}개" if label else f"{len(values)}개 항목"
        st.caption(title)
        items = "".join(f"<li>{html.escape(value)}</li>" for value in values)
        st.markdown(
            f'<ul class="list-result">{items}</ul>',
            unsafe_allow_html=True,
        )

    if full_df is not None and not full_df.empty and len(full_df.columns) > 1:
        with st.expander("전체 데이터 보기", expanded=False):
            height = min(360, max(120, 38 * (min(len(full_df), 12) + 1)))
            st.dataframe(
                for_display(full_df),
                use_container_width=True,
                hide_index=True,
                height=height,
            )


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
            st.dataframe(
                for_display(display_df.head(10)),
                use_container_width=True,
                hide_index=True,
                height=height,
            )
            return

    st.caption(f"표 결과 · {len(display_df):,}행")
    height = min(560, max(140, 38 * (min(len(display_df), 18) + 1)))
    st.dataframe(
        for_display(display_df),
        use_container_width=True,
        hide_index=True,
        height=height,
    )


def _same_frame(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    if left is right:
        return True
    if left.shape != right.shape:
        return False
    try:
        return left.reset_index(drop=True).equals(right.reset_index(drop=True))
    except Exception:
        return False


def _render_operation_result() -> None:
    result = st.session_state.get("operation_result")
    if result is None:
        return

    label = st.session_state.get("active_operation", "result")
    if isinstance(result, pd.DataFrame):
        st.caption(f"연산 결과 · {label}")
        st.dataframe(
            for_display(result),
            use_container_width=True,
            hide_index=True,
            height=min(220, max(100, 38 * (len(result) + 1))),
        )
        export_df = result
    else:
        try:
            display = f"{float(result):,.0f}"
        except (TypeError, ValueError):
            display = str(result)
        st.markdown(
            f"""
            <div class="result-box">
                <div class="label">결과 · {label}</div>
                <div class="value">{display}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        export_df = st.session_state.get("analysis_filter_df") or st.session_state.get(
            "selected_df"
        )

    if export_df is not None and isinstance(export_df, pd.DataFrame):
        buffer = io.BytesIO()
        export_df.to_excel(buffer, index=False, engine="openpyxl")
        st.download_button(
            "Excel 다운로드",
            data=buffer.getvalue(),
            file_name="analysis_result.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


def _render_chat_input() -> None:
    st.markdown("---")
    default = st.session_state.pop("pending_prompt", "") or ""
    nonce = st.session_state.get("chat_input_nonce", 0)
    if default:
        nonce += 1
        st.session_state.chat_input_nonce = nonce

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
                st.warning("동시 분석 모드에서는 파일 2개 이상을 선택하세요.")
                return
        elif get_analysis_df() is None:
            st.warning("먼저 엑셀 파일을 업로드하세요.")
            return
        prompt = prompt.strip()
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        st.session_state.pending_analysis_prompt = prompt
        st.rerun()
