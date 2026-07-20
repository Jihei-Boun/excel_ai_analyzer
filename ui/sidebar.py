"""좌측 사이드바 — Ollama 설정 · 분석 대상 파일 선택."""

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

        files = st.session_state.get("uploaded_files") or []
        if files:
            st.markdown('<p class="sidebar-label">분석할 파일</p>', unsafe_allow_html=True)
            from ui.upload import (
                activate_file,
                activate_files,
                get_active_named_frames,
                is_multi_analysis_mode,
            )

            if len(files) >= 2:
                st.caption(
                    "동시 분석 모드"
                    if is_multi_analysis_mode()
                    else "단일 파일 모드"
                )

            if is_multi_analysis_mode():
                ids = [meta["id"] for meta in files]
                labels = [meta["name"] for meta in files]
                default = [
                    label
                    for meta, label in zip(files, labels)
                    if meta["id"] in (st.session_state.get("active_file_ids") or [])
                ]
                picked = st.multiselect(
                    "동시 분석 대상",
                    options=labels,
                    default=default,
                )
                picked_ids = [ids[labels.index(label)] for label in picked]
                current_ids = list(st.session_state.get("active_file_ids") or [])
                if picked_ids != current_ids:
                    if len(picked_ids) >= 2:
                        activate_files(picked_ids, reset_analysis=True)
                        st.rerun()
                    elif len(picked_ids) == 1:
                        activate_file(picked_ids[0], reset_analysis=True)
                        st.rerun()
                    elif len(files) >= 2:
                        st.caption("동시 분석에는 파일 2개 이상을 선택하세요.")
                st.caption(f"{len(get_active_named_frames())}개 동시 분석 중")
            else:
                active_id = st.session_state.get("active_file_id")
                label_by_id = {f["id"]: f["name"] for f in files}
                ids = [f["id"] for f in files]
                if active_id not in ids:
                    active_id = ids[0]
                if st.session_state.get("sidebar_single_file") not in ids:
                    st.session_state.sidebar_single_file = active_id
                chosen = st.radio(
                    "분석할 파일 (하나만)",
                    options=ids,
                    format_func=lambda file_id: label_by_id[file_id],
                    key="sidebar_single_file",
                )
                if chosen != active_id:
                    activate_file(chosen, reset_analysis=True, sync_mode_radio=False)
                    st.rerun()
                st.caption("이 파일이 AI 분석 대상입니다")

            st.caption(f"{len(files)}개 업로드됨")


def _model_index(options: list[str], current: str) -> int:
    return options.index(current) if current in options else 0
