"""채팅 요청 처리 로직."""

from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from core.analyzer import (
    detect_aggregate_op,
    infer_context_label,
    resolve_filter_source,
)
from core.excel_loader import sanitize_dataframe
from core.file_summary import is_summary_request
from core.prompt_intent import _expects_plot
from core.prompt_router import (
    SingleRouteOutcome,
    needs_chart_context,
    route_multi_prompt,
    route_single_prompt,
)
from core.pandasai_config import _friendly_error
from ui.file_state import (
    find_file,
    frame_label_parts,
    get_active_named_frames,
    get_analysis_df,
    get_analysis_file_name,
    get_analysis_unit_label,
    is_multi_analysis_mode,
    is_multi_sheet_analysis,
)
from ui.session_store import clear_filter_selection_context, clear_filter_state


def _use_budget_profile() -> bool:
    return bool(st.session_state.get("budget_table_mode", False))


def process_user_prompt(prompt: str, *, user_already_added: bool = False) -> None:
    if is_multi_analysis_mode():
        named_frames = get_active_named_frames()
        if len(named_frames) < 2:
            unit = get_analysis_unit_label()
            st.warning(f"동시 분석 모드에서는 {unit} 2개 이상을 선택하세요.")
            return
        if not user_already_added:
            st.session_state.chat_messages.append({"role": "user", "content": prompt})

        unit = get_analysis_unit_label()
        with st.spinner(f"{len(named_frames)}개 {unit} 동시 분석 중..."):
            try:
                reply, extra_df, extra_meta = _run_multi_prompt(prompt, named_frames)
            except Exception as exc:
                reply = f"오류가 발생했습니다: {_friendly_error(exc)}"
                extra_df = None
                extra_meta = {}
                st.session_state.operation_result = None
    else:
        df: pd.DataFrame | None = get_analysis_df()
        if df is None:
            st.warning("먼저 엑셀 파일을 업로드하세요.")
            return

        df = sanitize_dataframe(df)
        active_id = st.session_state.get("active_file_id")
        if active_id:
            st.session_state.setdefault("file_frames", {})[active_id] = df
        st.session_state.df = df
        selected = st.session_state.get("selected_df")
        if selected is not None:
            st.session_state.selected_df = sanitize_dataframe(selected)
        filter_df = st.session_state.get("analysis_filter_df")
        if filter_df is not None:
            st.session_state.analysis_filter_df = sanitize_dataframe(filter_df)

        if not user_already_added:
            st.session_state.chat_messages.append({"role": "user", "content": prompt})

        with st.spinner("분석 중..."):
            try:
                reply, extra_df, extra_meta = _run_prompt(prompt, df)
            except Exception as exc:
                reply = f"오류가 발생했습니다: {_friendly_error(exc)}"
                extra_df = None
                extra_meta = {}
                st.session_state.operation_result = None
    message: dict = {"role": "assistant", "content": reply}
    message.update(extra_meta)
    if extra_df is not None:
        message["dataframe"] = extra_df
    st.session_state.chat_messages.append(message)
    st.rerun()


def _apply_route_outcome(outcome: SingleRouteOutcome) -> tuple[str, pd.DataFrame | None, dict]:
    if outcome.clear_operation:
        st.session_state.operation_result = None
        st.session_state.active_operation = None

    if outcome.reset_filter:
        clear_filter_selection_context()
        if outcome.filter_auto_reset:
            st.session_state.filter_auto_reset = True

    if outcome.set_filter_df is not None:
        st.session_state.analysis_filter_df = outcome.set_filter_df
        st.session_state.selected_df = outcome.set_filter_df

    if outcome.update_context_label:
        st.session_state.analysis_context_label = outcome.update_context_label

    if outcome.update_filter_summary:
        st.session_state.last_filter_summary = outcome.update_filter_summary

    if outcome.remember_aggregate and outcome.dataframe is not None:
        _remember_aggregate_result(
            outcome.dataframe,
            outcome.aggregate_prompt or "",
        )

    meta = dict(outcome.meta)
    if outcome.filter_auto_reset:
        meta = _attach_auto_reset_note(meta)

    if outcome.set_operation_result is not None:
        st.session_state.operation_result = outcome.set_operation_result
        st.session_state.active_operation = outcome.operation_name

    if outcome.dataframe is not None:
        reply, stored = _store_dataframe_result(
            outcome.dataframe,
            outcome.reply,
            keep_as_filter=outcome.keep_as_filter,
            replace_selection=outcome.replace_selection,
        )
        if meta.get("filter_summary"):
            st.session_state.last_filter_summary = meta["filter_summary"]
        return reply, stored, meta

    return outcome.reply, None, meta


def _run_prompt(
    prompt: str,
    df: pd.DataFrame,
) -> tuple[str, pd.DataFrame | None, dict]:
    source = _resolve_analysis_source(df, prompt)
    context_label = _resolve_context_label(source, prompt)

    outcome = route_single_prompt(
        prompt,
        full_df=df,
        source_df=source,
        context_label=context_label,
        base_url=st.session_state.ollama_base_url,
        model=st.session_state.ollama_model,
        use_budget_profile=_use_budget_profile(),
        prior_aggregate_df=st.session_state.get("last_aggregate_df"),
        prior_aggregate_prompt=st.session_state.get("last_analysis_prompt"),
        prior_user_prompt=_prior_user_analysis_prompt(),
        last_assistant_df=_last_assistant_dataframe(),
        summary_text=_build_summary_reply(df) if is_summary_request(prompt) else None,
    )
    return _apply_route_outcome(outcome)


def _store_dataframe_result(
    result: pd.DataFrame,
    summary: str,
    *,
    keep_as_filter: bool = False,
    replace_selection: bool = True,
) -> tuple[str, pd.DataFrame]:
    result = result.reset_index(drop=True)
    if keep_as_filter:
        st.session_state.analysis_filter_df = result
        st.session_state.selected_df = result
    elif replace_selection:
        st.session_state.selected_df = result
    else:
        filter_df = st.session_state.get("analysis_filter_df")
        if filter_df is not None and len(filter_df) > 0:
            st.session_state.selected_df = filter_df
    st.session_state.operation_result = None
    st.session_state.work_target = "분석 결과" if not result.empty else "원본 df"
    st.session_state.active_operation = None
    return summary, result


def _resolve_context_label(source: pd.DataFrame, prompt: str) -> str | None:
    """집계 표의 행 라벨: 저장된 필터명 → 필터 표의 분류값 → 이전 필터 질문의 셀 값."""
    stored = st.session_state.get("analysis_context_label")
    if stored and not _looks_like_meta_label(str(stored)):
        return str(stored)
    if stored and _looks_like_meta_label(str(stored)):
        # 이전 메타 질문으로 오염된 라벨은 버린다
        st.session_state.analysis_context_label = None

    filter_df = st.session_state.get("analysis_filter_df")
    work = filter_df if filter_df is not None and len(filter_df) > 0 else source

    label = infer_context_label(prompt=None, result_df=work, full_df=None)
    if label:
        st.session_state.analysis_context_label = label
        return label

    for message in reversed(st.session_state.get("chat_messages") or []):
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        if not content or content.strip() == prompt.strip():
            continue
        if detect_aggregate_op(content):
            continue
        if _is_meta_user_prompt(content):
            continue
        # 이전 질문에서는 실제 데이터 셀 값만 라벨로 인정 (문장 조각 추출 금지)
        label = infer_context_label(
            prompt=content,
            result_df=work,
            full_df=source,
            allow_prompt_text=False,
        )
        if label:
            st.session_state.analysis_context_label = label
            return label
    return None


def _is_meta_user_prompt(content: str) -> bool:
    """스키마/품질/요약/의미추정 등 집계 맥락이 아닌 메타 질문."""
    from core.file_summary import is_summary_request
    from core.quality import is_quality_request
    from core.schema_compare import is_column_meaning_request, is_schema_request

    return (
        is_schema_request(content)
        or is_column_meaning_request(content)
        or is_quality_request(content)
        or is_summary_request(content)
    )


def _looks_like_meta_label(label: str) -> bool:
    """오염된 메타 질문 잔여 라벨인지 판별한다."""
    compact = re.sub(r"\s+", "", str(label)).lower()
    tokens = (
        "컬럼",
        "의미",
        "추측",
        "설명",
        "품질",
        "수정",
        "타입",
        "구분",
        "스키마",
        "결측",
        "숫자",
        "문자",
    )
    return any(token in compact for token in tokens)


def _run_multi_prompt(
    prompt: str,
    named_frames: list[tuple[str, pd.DataFrame]],
) -> tuple[str, pd.DataFrame | None, dict]:
    prepared = [(name, sanitize_dataframe(frame)) for name, frame in named_frames]
    unit_label = get_analysis_unit_label()

    outcome = route_multi_prompt(
        prompt,
        named_frames=prepared,
        base_url=st.session_state.ollama_base_url,
        model=st.session_state.ollama_model,
        use_budget_profile=_use_budget_profile(),
        context_label=st.session_state.get("analysis_context_label"),
        filter_df=st.session_state.get("analysis_filter_df"),
        sheet_info=_multi_unit_sheet_info(prepared),
        unit_label=unit_label,
    )
    return _apply_route_outcome(outcome)


def _build_summary_reply(df: pd.DataFrame) -> str:
    """세션의 시트·경로 메타를 붙여 파일 요약을 만든다."""
    from core.file_summary import build_file_summary

    active_id = st.session_state.get("active_file_id")
    meta = find_file(active_id) if active_id else None
    file_name = get_analysis_file_name() or st.session_state.get("file_name")
    sheet_names = (
        (meta or {}).get("sheet_names")
        or st.session_state.get("sheet_names")
        or []
    )
    current_sheet = (
        (meta or {}).get("current_sheet")
        or st.session_state.get("current_sheet")
    )
    file_path = (meta or {}).get("path") or st.session_state.get("file_path")
    return build_file_summary(
        df,
        file_name=file_name,
        sheet_name=current_sheet,
        sheet_names=list(sheet_names) if sheet_names else None,
        file_path=file_path,
        use_budget_profile=_use_budget_profile(),
    )


def _multi_unit_sheet_info(
    named_frames: list[tuple[str, pd.DataFrame]],
) -> dict[str, dict]:
    """동시 분석 단위명 → 시트/경로 메타 매핑."""
    info: dict[str, dict] = {}
    files = st.session_state.get("uploaded_files") or []
    by_name = {meta.get("name"): meta for meta in files if meta.get("name")}

    if is_multi_sheet_analysis():
        active_id = st.session_state.get("active_file_id")
        meta = find_file(active_id) if active_id else None
        path = (meta or {}).get("path")
        for name, _frame in named_frames:
            info[name] = {
                "current_sheet": name,
                "sheet_names": [name],
                "path": path,
            }
        return info

    for name, _frame in named_frames:
        file_name, sheet = frame_label_parts(name)
        if file_name and sheet:
            meta = by_name.get(file_name) or {}
            info[name] = {
                "current_sheet": sheet,
                "sheet_names": meta.get("sheet_names"),
                "path": meta.get("path"),
            }
            continue
        meta = by_name.get(name) or {}
        info[name] = {
            "current_sheet": meta.get("current_sheet"),
            "sheet_names": meta.get("sheet_names"),
            "path": meta.get("path"),
        }
    return info


def _remember_aggregate_result(table: pd.DataFrame, prompt: str) -> None:
    """후속 '차트로 보여줘' 요청에 쓸 직전 집계 표를 저장한다."""
    if table is None or table.empty:
        return
    st.session_state.last_aggregate_df = table.reset_index(drop=True)
    st.session_state.last_analysis_prompt = prompt


def _prior_user_analysis_prompt() -> str | None:
    """바로 직전 사용자 분석 요청(차트-only 제외)을 찾는다."""
    messages = st.session_state.get("chat_messages") or []
    seen_current = False
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if not seen_current:
            seen_current = True
            continue
        if _expects_plot(content) and needs_chart_context(
            content,
            st.session_state.get("df") or pd.DataFrame(),
        ):
            continue
        return content
    return None


def _last_assistant_dataframe() -> pd.DataFrame | None:
    """채팅에 붙어 있는 직전 assistant 표."""
    for message in reversed(st.session_state.get("chat_messages") or []):
        if message.get("role") != "assistant":
            continue
        attached = message.get("dataframe")
        if isinstance(attached, pd.DataFrame) and not attached.empty:
            return attached.reset_index(drop=True)
    return None


def _resolve_analysis_source(df: pd.DataFrame, prompt: str) -> pd.DataFrame:
    """이전 필터 결과에 없는 값을 요청하면 원본 DataFrame으로 되돌린다."""
    filter_df = st.session_state.get("analysis_filter_df")
    selected = st.session_state.get("selected_df")
    active_filter = None
    if filter_df is not None and len(filter_df) > 0:
        active_filter = filter_df
    elif selected is not None and len(selected) > 0:
        active_filter = selected

    source, reset = resolve_filter_source(
        df,
        active_filter,
        prompt,
        keep_filter_for_aggregate=True,
    )
    if reset:
        clear_filter_state()
        st.session_state.filter_auto_reset = True
    elif selected is not None and len(selected) == 0:
        st.session_state.selected_df = None
        st.session_state.work_target = "원본 df"
    return source


def _attach_auto_reset_note(meta: dict) -> dict:
    """필터가 원본으로 자동 전환된 경우 안내 문구를 붙인다."""
    if st.session_state.pop("filter_auto_reset", False):
        meta["filter_note"] = "이전 필터에 해당 값이 없어 전체 데이터에서 다시 찾았습니다."
    return meta
