"""좌측 사이드바 — Ollama 설정 · 분석 대상 파일 선택."""

from __future__ import annotations

import requests
import streamlit as st

from core.constants import DEFAULT_OLLAMA_BASE_URL, OLLAMA_TIMEOUT_SEC
from core.profile_loader import (
    list_profile_names,
    load_profile,
    profile_display_label,
)


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


def render_sidebar() -> None:
    with st.sidebar:
        st.subheader("분석 상세")
        st.session_state.show_analysis_code = st.checkbox(
            "실행 코드 표시",
            value=bool(st.session_state.get("show_analysis_code", False)),
            help="PandasAI가 생성·실행한 코드를 채팅에서 확인합니다.",
        )
        _render_profile_selector()

        st.subheader("연결")
        st.session_state.ollama_base_url = st.text_input(
            "Ollama URL",
            value=st.session_state.ollama_base_url,
            placeholder=DEFAULT_OLLAMA_BASE_URL,
        )

        connected = _check_ollama(st.session_state.ollama_base_url)
        st.session_state.ollama_connected = connected
        if connected:
            st.success("Ollama 연결됨")
        else:
            st.error("Ollama에 연결할 수 없습니다")

        models = _fetch_ollama_models(st.session_state.ollama_base_url)
        model_options = models or [st.session_state.ollama_model]
        st.session_state.ollama_model = st.selectbox(
            "분석 모델",
            model_options,
            index=_model_index(model_options, st.session_state.ollama_model),
        )

        files = st.session_state.get("uploaded_files") or []
        if files:
            st.subheader("분석할 파일")
            from ui.file_state import (
                activate_file,
                activate_files,
                get_active_named_frames,
                get_analysis_unit_label,
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

                unit = get_analysis_unit_label()
                st.caption(
                    f"{len(get_active_named_frames())}개 {unit} 동시 분석 중"
                )
                _render_multi_file_sheet_selectors(
                    list(st.session_state.get("active_file_ids") or [])
                )
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

        st.caption(
            "테마 변경: 화면 우측 상단 ⋮(또는 ☰) → Settings → Theme 에서 "
            "Light / Dark / Use system setting 을 선택하세요."
        )


def _render_profile_selector() -> None:
    """도메인 프로필 선택. 업로드 기반 자동 추천 + 사용자 변경."""
    names = list(list_profile_names())
    if not names:
        names = ["generic", "budget"]
    # generic → budget → 나머지
    ordered = [n for n in ("generic", "budget") if n in names]
    ordered.extend(n for n in names if n not in ordered)

    current = str(st.session_state.get("analysis_profile") or "").strip().lower()
    if current not in ordered:
        current = (
            "budget"
            if st.session_state.get("budget_table_mode")
            else "generic"
        )
        if current not in ordered:
            current = ordered[0]
        st.session_state.analysis_profile = current

    def _mark_manual() -> None:
        st.session_state.profile_manually_set = True

    chosen = st.selectbox(
        "분석 프로필",
        options=ordered,
        format_func=profile_display_label,
        help=(
            "도메인 프로필은 추천 질문·라벨·footer·계획 가이던스에 영향을 줍니다. "
            "업로드 시 컬럼으로 자동 추천되며, 여기서 바꾸면 수동 고정됩니다."
        ),
        key="analysis_profile",
        on_change=_mark_manual,
    )
    st.session_state.budget_table_mode = chosen == "budget"

    suggested = str(st.session_state.get("suggested_profile") or "").strip().lower()
    score = int(st.session_state.get("suggested_profile_score") or 0)
    manual = bool(st.session_state.get("profile_manually_set"))
    try:
        profile = load_profile(chosen)
        domain = str(profile.get("domain") or chosen)
        if chosen == "budget":
            st.caption("예실대비표 특화 · footer 제외 · 예산 추천 질문")
        elif chosen == "generic":
            st.caption("일반 분석 (도메인 가정 없음)")
        else:
            st.caption(f"도메인: {domain}")
        if suggested and suggested != "generic" and score > 0:
            if not manual and chosen == suggested:
                st.caption(f"업로드 컬럼 기준 자동 추천 적용 (점수 {score})")
            elif manual and chosen != suggested:
                st.caption(
                    f"수동 선택 중 · 자동 추천은 {profile_display_label(suggested)}"
                )
            elif not manual and chosen != suggested:
                st.caption(
                    f"자동 추천: {profile_display_label(suggested)} (점수 {score})"
                )
    except Exception:  # noqa: BLE001
        st.caption(f"프로필: {chosen}")


def _render_multi_file_sheet_selectors(active_ids: list[str]) -> None:
    """다중 파일 모드에서 파일별 시트 다중 선택 UI."""
    from ui.file_state import find_file

    multi_sheet_files = []
    for file_id in active_ids:
        meta = find_file(file_id)
        if meta is None:
            continue
        if len(meta.get("sheet_names") or []) >= 2:
            multi_sheet_files.append((file_id, meta))

    if not multi_sheet_files:
        return

    st.subheader("파일별 분석 시트")
    st.caption(
        "각 파일에서 분석에 포함할 시트를 고르세요. "
        "2개 이상이면 시트 단위로 펼칩니다."
    )
    for file_id, meta in multi_sheet_files:
        st.markdown(f"**{meta['name']}**")
        _render_sheet_multiselect(file_id, label="포함할 시트")


def _render_sheet_multiselect(
    file_id: str,
    *,
    label: str = "동시 분석할 시트",
) -> None:
    """파일의 시트 다중 선택 UI (단일·다중 파일 공통)."""
    from ui.file_state import (
        _normalize_active_sheets,
        find_file,
        set_active_sheets,
    )

    meta = find_file(file_id)
    if meta is None:
        return
    sheet_names = list(meta.get("sheet_names") or [])
    if len(sheet_names) < 2:
        return

    current = list(_normalize_active_sheets(meta))
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
        label,
        options=sheet_names,
        help=(
            "2개 이상 선택하면 해당 파일의 시트를 각각 분석 단위로 펼칩니다. "
            "미리보기는 아래에서 시트를 따로 볼 수 있습니다."
        ),
        key=widget_key,
    )
    if not picked:
        st.caption("시트를 1개 이상 선택하세요.")
        return
    if list(picked) != list(current):
        set_active_sheets(list(picked), file_id=file_id, reset_analysis=True)
        st.rerun()
    if len(picked) >= 2:
        st.caption(f"시트 {len(picked)}개 포함")
    else:
        st.caption(f"현재 시트: {picked[0]}")


def _model_index(options: list[str], current: str) -> int:
    return options.index(current) if current in options else 0
