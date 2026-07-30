"""좌측 사이드바 — Ollama 설정 · 분석 대상 파일 선택."""

from __future__ import annotations

import requests
import streamlit as st

from core.constants import DEFAULT_OLLAMA_BASE_URL, OLLAMA_TIMEOUT_SEC


def _fetch_ollama_models(base_url: str) -> list[str]:
    try:
        response = requests.get(
            f"{base_url.rstrip('/')}/api/tags",
            timeout=OLLAMA_TIMEOUT_SEC,
        )
        response.raise_for_status()
        models = response.json().get("models", [])
        return [m["name"] for m in models if m.get("name")]
    except requests.RequestException:
        return []


def _check_ollama(base_url: str) -> bool:
    try:
        response = requests.get(
            f"{base_url.rstrip('/')}/api/tags",
            timeout=OLLAMA_TIMEOUT_SEC,
        )
        return response.ok
    except requests.RequestException:
        return False


_THEME_LABELS = {"dark": "다크", "light": "라이트"}
_THEME_VALUES = {"다크": "dark", "라이트": "light"}


def sync_theme_from_widget() -> None:
    """위젯 상태가 있으면 테마를 먼저 동기화한다 (스타일 주입 전)."""
    label = st.session_state.get("theme_radio")
    if label in _THEME_VALUES:
        st.session_state.theme = _THEME_VALUES[label]


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown('<p class="sidebar-label">외형</p>', unsafe_allow_html=True)
        current = st.session_state.get("theme", "dark")
        if current not in _THEME_LABELS:
            current = "dark"
        chosen_label = st.radio(
            "테마",
            options=list(_THEME_LABELS.values()),
            index=list(_THEME_LABELS).index(current),
            horizontal=True,
            label_visibility="collapsed",
            key="theme_radio",
        )
        st.session_state.theme = _THEME_VALUES[chosen_label]

        st.markdown('<p class="sidebar-label">분석 상세</p>', unsafe_allow_html=True)
        st.session_state.show_analysis_code = st.checkbox(
            "실행 코드 표시",
            value=bool(st.session_state.get("show_analysis_code", False)),
            help="PandasAI가 생성·실행한 코드를 채팅에서 확인합니다.",
        )
        st.session_state.budget_table_mode = st.checkbox(
            "예산 표 모드",
            value=bool(st.session_state.get("budget_table_mode", False)),
            help="예실대비표 전용 요약·하단 요약행(내부흡수액·외부유출액) 제외를 사용합니다.",
        )

        st.markdown('<p class="sidebar-label">연결</p>', unsafe_allow_html=True)

        st.session_state.ollama_base_url = st.text_input(
            "Ollama URL",
            value=st.session_state.ollama_base_url,
            placeholder=DEFAULT_OLLAMA_BASE_URL,
        )

        connected = _check_ollama(st.session_state.ollama_base_url)
        st.session_state.ollama_connected = connected
        if connected:
            st.markdown('<div class="conn-ok">● Ollama 연결됨</div>', unsafe_allow_html=True)
        else:
            st.markdown('<p class="conn-fail">Ollama에 연결할 수 없습니다</p>', unsafe_allow_html=True)

        models = _fetch_ollama_models(st.session_state.ollama_base_url)
        model_options = models or [st.session_state.ollama_model]
        # selectbox는 Streamlit 다크 테마 잔여로 라이트에서 검게 남는 경우가 있어 radio 사용
        st.session_state.ollama_model = st.radio(
            "분석 모델",
            model_options,
            index=_model_index(model_options, st.session_state.ollama_model),
        )

        files = st.session_state.get("uploaded_files") or []
        if files:
            st.markdown('<p class="sidebar-label">분석할 파일</p>', unsafe_allow_html=True)
            from ui.file_state import (
                activate_file,
                activate_files,
                get_active_named_frames,
                is_multi_file_analysis,
            )

            if len(files) >= 2:
                st.caption(
                    "파일 동시 분석 모드"
                    if is_multi_file_analysis()
                    else "단일 파일 모드"
                )

            if is_multi_file_analysis():
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
                st.caption(f"{len(get_active_named_frames())}개 파일 동시 분석 중")
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
                _render_sheet_multiselect(chosen)

            st.caption(f"{len(files)}개 업로드됨")


def _render_sheet_multiselect(file_id: str) -> None:
    """단일 파일 모드에서 시트 다중 선택 UI."""
    from ui.file_state import find_file, get_active_sheet_names, set_active_sheets

    meta = find_file(file_id)
    if meta is None:
        return
    sheet_names = list(meta.get("sheet_names") or [])
    if len(sheet_names) < 2:
        return

    st.markdown('<p class="sidebar-label">분석할 시트</p>', unsafe_allow_html=True)
    current = get_active_sheet_names() or [meta.get("current_sheet") or sheet_names[0]]
    desired = [name for name in current if name in sheet_names]
    if not desired:
        desired = [sheet_names[0]]

    # key만 쓰고 default는 쓰지 않는다 (Streamlit에서 값 불일치 원인).
    widget_key = f"sidebar_sheets_{file_id}"
    if widget_key not in st.session_state:
        st.session_state[widget_key] = desired
    else:
        # 옵션에 없는 값 제거
        stored = [
            name
            for name in (st.session_state.get(widget_key) or [])
            if name in sheet_names
        ]
        if not stored:
            st.session_state[widget_key] = desired
        elif stored != st.session_state.get(widget_key):
            st.session_state[widget_key] = stored

    picked = st.multiselect(
        "동시 분석할 시트",
        options=sheet_names,
        help="2개 이상 선택하면 시트별로 동시에 분석합니다. 미리보기는 아래에서 시트를 따로 볼 수 있습니다.",
        key=widget_key,
    )
    if not picked:
        st.caption("시트를 1개 이상 선택하세요.")
        return
    if list(picked) != list(current):
        set_active_sheets(list(picked), file_id=file_id, reset_analysis=True)
        st.rerun()
    if len(picked) >= 2:
        st.caption(f"시트 {len(picked)}개 동시 분석 중")
    else:
        st.caption(f"현재 시트: {picked[0]}")


def _model_index(options: list[str], current: str) -> int:
    return options.index(current) if current in options else 0
