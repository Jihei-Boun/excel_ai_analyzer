"""session_state 초기화 및 관리."""

from __future__ import annotations

import streamlit as st

RECOMMENDED_PROMPTS = (
    "각 컬럼의 데이터 타입과 결측치 개수를 알려줘",
    "첫 번째 숫자형 컬럼의 합계를 구해줘",
    "범주형 컬럼별 행 개수를 표로 보여줘",
    "숫자형 컬럼들의 기초 통계를 보여줘",
)
MULTI_FILE_PROMPTS = (
    "각 파일의 행 수와 컬럼 목록을 비교해줘",
    "두 파일에서 공통으로 있는 컬럼을 알려줘",
    "각 파일의 숫자형 컬럼 합계를 표로 비교해줘",
    "두 파일을 공통 키로 병합한 결과를 보여줘",
)
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
        "active_file_id": None,
        "active_file_ids": [],
        "preview_file_id": None,
        "analysis_mode": "single",  # "single" | "multi"
        "uploader_nonce": 0,
        "selected_df": None,
        "analysis_filter_df": None,
        "operation_result": None,
        "chat_messages": [],
        "ollama_base_url": "http://localhost:11434",
        "ollama_model": "qwen2.5:7b",
        "ollama_connected": False,
        "work_target": "원본 df",
        "active_operation": None,
        "pending_prompt": "",
        "pending_analysis_prompt": "",
        "chat_input_nonce": 0,
        "last_filter_summary": "",
        "analysis_context_label": None,
        "_df_sanitized": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_work_state(*, clear_chat: bool = True) -> None:
    """분석 결과·연산 상태를 초기화한다. clear_chat=True면 대화도 비운다."""
    st.session_state.selected_df = None
    st.session_state.analysis_filter_df = None
    st.session_state.operation_result = None
    st.session_state.work_target = "원본 df"
    st.session_state.active_operation = None
    st.session_state.last_filter_summary = ""
    st.session_state.analysis_context_label = None
    if clear_chat:
        st.session_state.chat_messages = []
        st.session_state.pending_prompt = ""
        st.session_state.pending_analysis_prompt = ""
        st.session_state.chat_input_nonce = (
            st.session_state.get("chat_input_nonce", 0) + 1
        )
