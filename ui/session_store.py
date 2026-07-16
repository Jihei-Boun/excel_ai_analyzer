"""session_state 초기화 및 관리."""

from __future__ import annotations

import streamlit as st

RECOMMENDED_PROMPTS = (
    "각 컬럼의 데이터 타입과 결측치 개수를 알려줘",
    "첫 번째 숫자형 컬럼의 합계를 구해줘",
    "범주형 컬럼별 행 개수를 표로 보여줘",
    "숫자형 컬럼들의 기초 통계를 보여줘",
)
QUICK_OPERATIONS = (
    ("합계", "sum"),
    ("평균", "mean"),
    ("최댓값", "max"),
    ("최솟값", "min"),
    ("정렬", "sort"),
    ("상위 N", "topn"),
    ("필터", "filter"),
    ("초기화", "reset"),
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
        "uploader_nonce": 0,
        "selected_df": None,
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
        "_df_sanitized": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
