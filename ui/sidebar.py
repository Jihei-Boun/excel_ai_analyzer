"""좌측 사이드바 — Ollama 설정만."""

from __future__ import annotations

import requests
import streamlit as st


def _fetch_ollama_models(base_url: str) -> list[str]:
    try:
        response = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
        response.raise_for_status()
        models = response.json().get("models", [])
        return [m["name"] for m in models if m.get("name")]
    except requests.RequestException:
        return []


def _check_ollama(base_url: str) -> bool:
    try:
        response = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
        return response.ok
    except requests.RequestException:
        return False


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown('<p class="sidebar-label">연결</p>', unsafe_allow_html=True)

        st.session_state.ollama_base_url = st.text_input(
            "Ollama URL",
            value=st.session_state.ollama_base_url,
            placeholder="http://localhost:11434",
        )

        connected = _check_ollama(st.session_state.ollama_base_url)
        st.session_state.ollama_connected = connected
        if connected:
            st.markdown('<div class="conn-ok">● Ollama 연결됨</div>', unsafe_allow_html=True)
        else:
            st.markdown('<p class="conn-fail">Ollama에 연결할 수 없습니다</p>', unsafe_allow_html=True)

        models = _fetch_ollama_models(st.session_state.ollama_base_url)
        model_options = models or [st.session_state.ollama_model]

        st.session_state.ollama_model = st.selectbox(
            "분석 모델",
            model_options,
            index=_model_index(model_options, st.session_state.ollama_model),
        )

        with st.expander("고급 설정", expanded=False):
            st.session_state.intent_model = st.selectbox(
                "의도 분석 모델",
                model_options,
                index=_model_index(model_options, st.session_state.intent_model),
            )

        files = st.session_state.get("uploaded_files") or []
        if files:
            st.markdown('<p class="sidebar-label">파일</p>', unsafe_allow_html=True)
            active_id = st.session_state.get("active_file_id")
            labels = [f["name"] for f in files]
            ids = [f["id"] for f in files]
            current_idx = ids.index(active_id) if active_id in ids else 0
            chosen = st.radio(
                "활성 파일",
                labels,
                index=current_idx,
                label_visibility="collapsed",
            )
            chosen_id = ids[labels.index(chosen)]
            if chosen_id != active_id:
                from ui.upload import activate_file

                activate_file(chosen_id, reset_analysis=True)
                st.rerun()

            sheet = st.session_state.get("current_sheet")
            if sheet and len(st.session_state.get("sheet_names") or []) > 1:
                st.caption(f"시트: {sheet}")
            st.caption(f"{len(files)}개 업로드됨")


def _model_index(options: list[str], current: str) -> int:
    return options.index(current) if current in options else 0
