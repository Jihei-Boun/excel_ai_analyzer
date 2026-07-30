"""session_state 초기화 및 관리."""

from __future__ import annotations

import streamlit as st

from core.constants import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_MODEL,
)

RECOMMENDED_PROMPTS = (
    "파일을 요약해줘",
    "각 컬럼의 데이터 타입과 결측치 개수를 알려줘",
    "첫 번째 숫자형 컬럼의 합계를 구해줘",
    "범주형 컬럼별 행 개수를 표로 보여줘",
)
MULTI_FILE_PROMPTS = (
    "각 파일의 행 수와 컬럼 목록을 비교해줘",
    "두 파일에서 공통으로 있는 컬럼을 알려줘",
    "각 파일의 숫자형 컬럼 합계를 표로 비교해줘",
    "두 파일을 공통 키로 병합한 결과를 보여줘",
)
MULTI_SHEET_PROMPTS = (
    "각 시트의 행 수와 컬럼 목록을 비교해줘",
    "시트별로 숫자형 컬럼 합계를 표로 비교해줘",
    "시트 간 공통 컬럼을 알려줘",
    "파일을 요약해줘",
)

ANALYSIS_RESULT_KEYS = (
    "selected_df",
    "analysis_filter_df",
    "operation_result",
    "analysis_context_label",
    "last_filter_summary",
    "last_aggregate_df",
    "last_analysis_prompt",
    "active_operation",
)


def clear_filter_state(*, work_target: str = "원본 df") -> None:
    """필터 관련 필드만 초기화 (자동 필터 리셋 등)."""
    st.session_state.selected_df = None
    st.session_state.analysis_filter_df = None
    st.session_state.analysis_context_label = None
    st.session_state.last_filter_summary = ""
    st.session_state.work_target = work_target


def clear_filter_selection_context() -> None:
    """필터 df·선택·컨텍스트 라벨만 초기화 (프롬프트 라우트 reset_filter)."""
    st.session_state.selected_df = None
    st.session_state.analysis_filter_df = None
    st.session_state.analysis_context_label = None


def clear_analysis_result_state(*, work_target: str | None = None) -> None:
    """필터/선택/연산/컨텍스트를 초기화. 채팅·업로드는 건드리지 않는다."""
    st.session_state.selected_df = None
    st.session_state.analysis_filter_df = None
    st.session_state.operation_result = None
    st.session_state.analysis_context_label = None
    st.session_state.last_filter_summary = ""
    st.session_state.last_aggregate_df = None
    st.session_state.last_analysis_prompt = ""
    st.session_state.active_operation = None
    if work_target is not None:
        st.session_state.work_target = work_target


def clear_selection_and_operation() -> None:
    """선택 행과 연산 결과만 초기화 (미리보기 시트 전환 등)."""
    st.session_state.selected_df = None
    st.session_state.operation_result = None


def init_session_state() -> None:
    defaults = {
        "df": None,
        "file_name": None,
        "file_path": None,
        "file_size": None,
        "sheet_names": [],
        "current_sheet": None,
        "uploaded_files": [],
        "file_frames": {},
        "sheet_frames": {},
        "active_file_id": None,
        "active_file_ids": [],
        "preview_file_id": None,
        "analysis_mode": "single",  # "single" | "multi"
        "uploader_nonce": 0,
        "selected_df": None,
        "analysis_filter_df": None,
        "operation_result": None,
        "chat_messages": [],
        "ollama_base_url": DEFAULT_OLLAMA_BASE_URL,
        "ollama_model": DEFAULT_OLLAMA_MODEL,
        "ollama_connected": False,
        "work_target": "원본 df",
        "active_operation": None,
        "pending_prompt": "",
        "pending_analysis_prompt": "",
        "chat_input_nonce": 0,
        "last_filter_summary": "",
        "analysis_context_label": None,
        "last_aggregate_df": None,
        "last_analysis_prompt": "",
        "_df_sanitized": False,
        "theme": "dark",  # "dark" | "light"
        "show_analysis_code": False,
        "budget_table_mode": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_work_state(*, clear_chat: bool = True) -> None:
    """분석 결과·연산 상태를 초기화한다. clear_chat=True면 대화도 비운다."""
    clear_analysis_result_state(work_target="원본 df")
    if clear_chat:
        st.session_state.chat_messages = []
        st.session_state.pending_prompt = ""
        st.session_state.pending_analysis_prompt = ""
        st.session_state.chat_input_nonce = (
            st.session_state.get("chat_input_nonce", 0) + 1
        )
