"""채팅 요청 처리 로직."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.analyzer import (
    _expects_dataframe,
    _expects_plot,
    _filter_by_mentioned_value,
    _filter_multi_by_mentioned_value,
    _is_complex_analysis,
    _normalize_text,
    build_context_aggregate_table,
    build_filter_summary,
    build_groupby_aggregate_table,
    build_multi_context_aggregate_table,
    detect_aggregate_op,
    extract_matched_value,
    find_groupby_column,
    find_mentioned_numeric_columns,
    infer_context_label,
    is_metric_aggregate_request,
    resolve_filter_source,
    run_analysis,
    run_multi_analysis,
    scalar_to_context_table,
    split_frames_by_source,
)
from core.chart_utils import generate_fallback_chart
from core.excel_loader import sanitize_dataframe
from core.file_summary import build_file_summary, build_multi_file_summary, is_summary_request
from core.result_format import exclude_aggregate_rows, to_list_display
from ui.upload import (
    find_file,
    get_active_named_frames,
    get_analysis_df,
    get_analysis_file_name,
    is_multi_analysis_mode,
)


def _use_budget_profile() -> bool:
    return bool(st.session_state.get("budget_table_mode", False))




def _merge_analysis_meta(meta: dict, analysis_meta: dict | None) -> dict:
    """PandasAI/라우팅 메타(code, chart_path)를 채팅 메시지 메타에 합친다."""
    if not analysis_meta:
        return meta
    for key in ("code", "chart_path"):
        value = analysis_meta.get(key)
        if value:
            meta[key] = value
    return meta


def _attach_filter_summary(
    meta: dict,
    *,
    prompt: str,
    result: pd.DataFrame,
    full_df: pd.DataFrame | None,
) -> dict:
    summary = build_filter_summary(prompt, result, full_df)
    if summary:
        meta["filter_summary"] = summary
        st.session_state.last_filter_summary = summary
    return meta

def process_user_prompt(prompt: str, *, user_already_added: bool = False) -> None:
    if is_multi_analysis_mode():
        named_frames = get_active_named_frames()
        if len(named_frames) < 2:
            st.warning("동시 분석 모드에서는 파일 2개 이상을 선택하세요.")
            return
        if not user_already_added:
            st.session_state.chat_messages.append({"role": "user", "content": prompt})

        with st.spinner(f"{len(named_frames)}개 파일 동시 분석 중..."):
            try:
                reply, extra_df, extra_meta = _run_multi_prompt(prompt, named_frames)
            except Exception as exc:
                reply = f"오류가 발생했습니다: {exc}"
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
                reply = f"오류가 발생했습니다: {exc}"
                extra_df = None
                extra_meta = {}
                st.session_state.operation_result = None

    message: dict = {"role": "assistant", "content": reply}
    message.update(extra_meta)
    if extra_df is not None:
        message["dataframe"] = extra_df
    st.session_state.chat_messages.append(message)
    st.rerun()


def _run_prompt(
    prompt: str,
    df: pd.DataFrame,
) -> tuple[str, pd.DataFrame | None, dict]:
    base_url = st.session_state.ollama_base_url
    model = st.session_state.ollama_model

    # 파일 요약은 LLM/차트 대신 규칙 기반 분석을 우선한다.
    if is_summary_request(prompt):
        reply = _build_summary_reply(df)
        st.session_state.operation_result = None
        st.session_state.active_operation = None
        return reply, None, {}

    source = _resolve_analysis_source(df, prompt)
    context_label = _resolve_context_label(source, prompt)

    # "리스트와 차트" 등 표+차트 동시 요청 판별
    wants_plot = _expects_plot(prompt)
    wants_table = _wants_table_and_chart(prompt)

    # 집계 표를 먼저 시도 (표+차트 동시 요청이면 표를 만든 뒤 차트도 생성)
    grouped = build_groupby_aggregate_table(
        source,
        prompt,
        use_budget_profile=_use_budget_profile(),
    )
    if grouped is not None:
        table, summary = grouped
        _remember_aggregate_result(table, prompt)
        meta: dict = {}
        if wants_plot:
            chart_path = generate_fallback_chart(table, prompt)
            if chart_path:
                meta["chart_path"] = chart_path
        reply, result = _store_dataframe_result(
            table,
            summary,
            keep_as_filter=False,
            replace_selection=False,
        )
        return reply, result, meta

    if not wants_plot or wants_table:
        contextual = build_context_aggregate_table(
            source,
            prompt,
            context_label=context_label,
        )
        if contextual is not None:
            table, summary = contextual
            _remember_aggregate_result(table, prompt)
            meta = {}
            if wants_plot:
                chart_path = generate_fallback_chart(table, prompt)
                if chart_path:
                    meta["chart_path"] = chart_path
            reply, result = _store_dataframe_result(
                table,
                summary,
                keep_as_filter=False,
                replace_selection=False,
            )
            return reply, result, meta

    # 차트만 요청 (집계 표 없이): 직전 집계 표와 동일 데이터 사용
    if wants_plot and not wants_table:
        chart_table, chart_prompt = _resolve_chart_table(
            source,
            prompt,
            context_label=context_label,
        )
        if chart_table is not None and not chart_table.empty:
            chart_path = generate_fallback_chart(chart_table, chart_prompt)
            if chart_path:
                st.session_state.operation_result = None
                st.session_state.active_operation = None
                return "차트 결과를 생성했습니다.", None, {"chart_path": chart_path}

    result, summary, analysis_meta = run_analysis(
        source,
        prompt,
        base_url=base_url,
        model=model,
        use_budget_profile=_use_budget_profile(),
    )

    if (
        isinstance(result, pd.DataFrame)
        and result.empty
        and source is not df
        and len(df) > 0
    ):
        result, summary, analysis_meta = run_analysis(
            df,
            prompt,
            base_url=base_url,
            model=model,
            use_budget_profile=_use_budget_profile(),
        )
        if isinstance(result, pd.DataFrame) and not result.empty:
            st.session_state.selected_df = None
            st.session_state.analysis_filter_df = None
            st.session_state.analysis_context_label = None
            st.session_state.filter_auto_reset = True
            source = df

    if analysis_meta.get("chart_path"):
        meta = _merge_analysis_meta({}, analysis_meta)
        st.session_state.operation_result = None
        st.session_state.active_operation = None
        return summary or "차트 결과를 생성했습니다.", None, meta

    if isinstance(result, pd.DataFrame):
        result = result.reset_index(drop=True)
        _update_context_from_filter(df, prompt, result)
        result, summary, meta = _postprocess_table_result(
            result,
            prompt,
            summary,
            source_df=source,
        )
        meta = _merge_analysis_meta(meta, analysis_meta)
        meta = _attach_auto_reset_note(meta)
        is_filter = detect_aggregate_op(prompt) is None
        if is_filter:
            meta = _attach_filter_summary(
                meta,
                prompt=prompt,
                result=result,
                full_df=df,
            )
        reply, stored = _store_dataframe_result(
            result,
            summary,
            keep_as_filter=is_filter,
            replace_selection=True,
        )
        return reply, stored, meta

    meta = _merge_analysis_meta({}, analysis_meta)
    if meta.get("chart_path"):
        st.session_state.operation_result = None
        st.session_state.active_operation = None
        return summary or "차트 결과를 생성했습니다.", None, meta

    # 숫자만 온 경우에도 맥락 요약 표로 변환
    if detect_aggregate_op(prompt) is not None:
        table = scalar_to_context_table(
            result,
            prompt,
            source,
            context_label=context_label,
        )
        if table is not None:
            reply, stored = _store_dataframe_result(
                table,
                summary or f"{context_label or '합계'} 집계 결과",
                keep_as_filter=False,
                replace_selection=False,
            )
            return reply, stored, meta

    st.session_state.operation_result = result
    st.session_state.active_operation = "PandasAI"
    return summary, None, meta


def _postprocess_table_result(
    result: pd.DataFrame,
    prompt: str,
    summary: str,
    *,
    source_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, str, dict]:
    """집계 행 제거·리스트 표시 메타를 적용한다."""
    meta: dict = {}
    if detect_aggregate_op(prompt) is None:
        result, excluded = exclude_aggregate_rows(result, prompt)
        if excluded:
            summary = f"{summary} · 합계·소계 {excluded}행 제외"

    list_info = to_list_display(result, prompt, source_df=source_df)
    if list_info is not None:
        meta["list_values"] = list_info.values
        meta["list_label"] = list_info.label
        if list_info.groups:
            meta["list_groups"] = list_info.groups

    return result, summary, meta


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
        # 집계 등: 필터/선택 데이터는 그대로 두고 결과 표만 채팅에 전달
        filter_df = st.session_state.get("analysis_filter_df")
        if filter_df is not None and len(filter_df) > 0:
            st.session_state.selected_df = filter_df
    st.session_state.operation_result = None
    st.session_state.work_target = "분석 결과" if not result.empty else "원본 df"
    st.session_state.active_operation = None
    return summary, result


def _update_context_from_filter(
    full_df: pd.DataFrame,
    prompt: str,
    result: pd.DataFrame,
) -> None:
    """리스트/필터 결과에서 다음 집계용 맥락 라벨을 저장한다."""
    if detect_aggregate_op(prompt) is not None:
        return
    if result is None or result.empty:
        return

    label = infer_context_label(prompt=prompt, result_df=result, full_df=full_df)
    if label:
        st.session_state.analysis_context_label = label
    summary = build_filter_summary(prompt, result, full_df)
    if summary:
        st.session_state.last_filter_summary = summary
    elif label:
        st.session_state.last_filter_summary = label


def _resolve_context_label(source: pd.DataFrame, prompt: str) -> str | None:
    """집계 표의 행 라벨: 저장된 필터명 → 필터 표의 분류값 → 이전 질문."""
    stored = st.session_state.get("analysis_context_label")
    if stored:
        return str(stored)

    filter_df = st.session_state.get("analysis_filter_df")
    work = filter_df if filter_df is not None and len(filter_df) > 0 else source

    # 현재 집계 질문이 아니라, 필터 결과/이전 질문에서만 라벨을 뽑는다
    label = infer_context_label(prompt=None, result_df=work, full_df=None)
    if label:
        st.session_state.analysis_context_label = label
        return label

    for message in reversed(st.session_state.get("chat_messages") or []):
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        if detect_aggregate_op(content):
            continue
        label = infer_context_label(
            prompt=content,
            result_df=work,
            full_df=source,
        )
        if label:
            st.session_state.analysis_context_label = label
            return label
    return None


def _run_multi_prompt(
    prompt: str,
    named_frames: list[tuple[str, pd.DataFrame]],
) -> tuple[str, pd.DataFrame | None, dict]:
    base_url = st.session_state.ollama_base_url
    model = st.session_state.ollama_model
    prepared = [(name, sanitize_dataframe(df)) for name, df in named_frames]

    if is_summary_request(prompt):
        sheet_info = _multi_sheet_info(prepared)
        reply = build_multi_file_summary(
            prepared,
            sheet_info=sheet_info,
            use_budget_profile=_use_budget_profile(),
        )
        st.session_state.operation_result = None
        st.session_state.active_operation = None
        return reply, None, {}

    # 집계: 이전 필터 기준 파일별 요약 표 (표+차트 동시 요청도 여기서 처리)
    if detect_aggregate_op(prompt) is not None:
        source_named, context_label = _resolve_multi_aggregate_source(prepared, prompt)
        contextual = build_multi_context_aggregate_table(
            source_named,
            prompt,
            context_label=context_label,
        )
        if contextual is not None:
            table, summary = contextual
            meta: dict = {}
            if _expects_plot(prompt):
                chart_path = generate_fallback_chart(table, prompt)
                if chart_path:
                    meta["chart_path"] = chart_path
                if not _wants_table_and_chart(prompt):
                    st.session_state.operation_result = None
                    st.session_state.active_operation = None
                    return summary or "차트 결과를 생성했습니다.", None, meta
            reply, stored = _store_dataframe_result(
                table,
                summary,
                keep_as_filter=False,
                replace_selection=False,
            )
            return reply, stored, meta

    result, summary, analysis_meta = run_multi_analysis(
        prepared,
        prompt,
        base_url=base_url,
        model=model,
        use_budget_profile=_use_budget_profile(),
    )

    if analysis_meta.get("chart_path"):
        meta = _merge_analysis_meta({}, analysis_meta)
        st.session_state.operation_result = None
        st.session_state.active_operation = None
        return summary or "차트 결과를 생성했습니다.", None, meta

    if isinstance(result, pd.DataFrame):
        result = result.reset_index(drop=True)
        is_filter = detect_aggregate_op(prompt) is None
        if is_filter:
            _update_context_from_filter(result, prompt, result)
        multi_source = st.session_state.get("analysis_filter_df")
        if multi_source is None or len(multi_source) == 0:
            parts = []
            for name, frame in prepared:
                part = frame.copy()
                part.insert(0, "출처파일", name)
                parts.append(part)
            multi_source = pd.concat(parts, ignore_index=True) if parts else None
        result, summary, meta = _postprocess_table_result(
            result,
            prompt,
            summary,
            source_df=multi_source,
        )
        meta = _merge_analysis_meta(meta, analysis_meta)
        if is_filter:
            meta = _attach_filter_summary(
                meta,
                prompt=prompt,
                result=result,
                full_df=multi_source,
            )
        reply, stored = _store_dataframe_result(
            result,
            summary,
            keep_as_filter=is_filter,
            replace_selection=True,
        )
        return reply, stored, meta

    meta = _merge_analysis_meta({}, analysis_meta)
    if meta.get("chart_path"):
        st.session_state.operation_result = None
        st.session_state.active_operation = None
        return summary, None, meta

    st.session_state.operation_result = result
    st.session_state.active_operation = "PandasAI (다중)"
    return summary, None, meta


def _resolve_multi_aggregate_source(
    prepared: list[tuple[str, pd.DataFrame]],
    prompt: str,
) -> tuple[list[tuple[str, pd.DataFrame]], str | None]:
    """집계에 쓸 파일별 데이터와 행 맥락 라벨을 결정한다."""
    context_label = st.session_state.get("analysis_context_label")
    filter_df = st.session_state.get("analysis_filter_df")
    metric_aggregate = is_metric_aggregate_request(prompt, named_dfs=prepared)

    # 프롬프트에 새 분류값이 있으면 그걸로 다시 필터 (이전 필터와 다르면)
    prompt_filtered = None
    prompt_label = None
    if not metric_aggregate:
        prompt_filtered = _filter_multi_by_mentioned_value(prepared, prompt)
        if prompt_filtered is not None and not prompt_filtered.empty:
            prompt_label = infer_context_label(
                prompt=prompt,
                result_df=prompt_filtered,
                full_df=None,
            ) or extract_matched_value(prompt_filtered, prompt)

    reuse_filter = (
        filter_df is not None
        and len(filter_df) > 0
        and "출처파일" in filter_df.columns
    )
    if reuse_filter and prompt_label and context_label:
        if _normalize_text(str(prompt_label)) != _normalize_text(str(context_label)):
            reuse_filter = False
    if reuse_filter and prompt_label and prompt_filtered is not None:
        # 이전 필터에 해당 값이 없으면 갱신
        on_filter = _filter_by_mentioned_value(filter_df, prompt)
        if on_filter is None or on_filter.empty:
            reuse_filter = False

    if reuse_filter:
        parts = split_frames_by_source(filter_df)
        if parts:
            if not context_label:
                context_label = infer_context_label(
                    prompt=None,
                    result_df=filter_df,
                    full_df=None,
                )
                if context_label:
                    st.session_state.analysis_context_label = context_label
            return parts, str(context_label) if context_label else None

    if prompt_filtered is not None and not prompt_filtered.empty:
        label = prompt_label or infer_context_label(
            prompt=prompt,
            result_df=prompt_filtered,
            full_df=None,
        )
        st.session_state.analysis_filter_df = prompt_filtered
        st.session_state.selected_df = prompt_filtered
        if label:
            st.session_state.analysis_context_label = label
            context_label = label
        return split_frames_by_source(prompt_filtered), (
            str(context_label) if context_label else None
        )

    if not context_label:
        context_label = infer_context_label(prompt=prompt, result_df=None, full_df=None)
    return prepared, str(context_label) if context_label else None


def _build_summary_reply(df: pd.DataFrame) -> str:
    """세션의 시트·경로 메타를 붙여 파일 요약을 만든다."""
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


def _multi_sheet_info(
    named_frames: list[tuple[str, pd.DataFrame]],
) -> dict[str, dict]:
    """파일명 → 시트/경로 메타 매핑."""
    info: dict[str, dict] = {}
    files = st.session_state.get("uploaded_files") or []
    by_name = {meta.get("name"): meta for meta in files if meta.get("name")}
    for name, _frame in named_frames:
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


_TABLE_AND_CHART_KEYWORDS = (
    "리스트와 차트",
    "리스트와차트",
    "표와 차트",
    "표와차트",
    "차트와 리스트",
    "차트와리스트",
    "차트와 표",
    "차트와표",
    "리스트로도",
    "표로도",
    "차트로도",
)


def _wants_table_and_chart(prompt: str) -> bool:
    """표(리스트)와 차트 둘 다 요청했는지."""
    lowered = prompt.lower()
    return any(kw in lowered for kw in _TABLE_AND_CHART_KEYWORDS)


def _needs_chart_context(prompt: str, df: pd.DataFrame) -> bool:
    """무엇을 그릴지 명시 없이 차트만 요청했는지."""
    if not _expects_plot(prompt):
        return False
    if find_groupby_column(df, prompt) is not None:
        return False
    if find_mentioned_numeric_columns(df, prompt):
        return False
    if is_metric_aggregate_request(prompt, df):
        return False
    return True


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
        if _expects_plot(content) and _needs_chart_context(content, st.session_state.get("df") or pd.DataFrame()):
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


def _resolve_chart_table(
    source: pd.DataFrame,
    prompt: str,
    *,
    context_label: str | None,
) -> tuple[pd.DataFrame | None, str]:
    """차트에 쓸 DataFrame과 컬럼 해석용 프롬프트를 결정한다."""
    chart_prompt = prompt

    # 1) 같은 요청에 그룹·수치가 있으면 그대로 집계 후 차트
    if not _needs_chart_context(prompt, source):
        grouped = build_groupby_aggregate_table(
            source,
            prompt,
            use_budget_profile=_use_budget_profile(),
        )
        if grouped is not None:
            return grouped[0], prompt
        contextual = build_context_aggregate_table(
            source,
            prompt,
            context_label=context_label,
        )
        if contextual is not None:
            return contextual[0], prompt
        return None, prompt

    # 2) 직전 집계 표 재사용
    stored = st.session_state.get("last_aggregate_df")
    stored_prompt = str(st.session_state.get("last_analysis_prompt") or "")
    if isinstance(stored, pd.DataFrame) and not stored.empty:
        return stored, stored_prompt or prompt

    # 3) 채팅 assistant 표
    attached = _last_assistant_dataframe()
    if attached is not None:
        prior = _prior_user_analysis_prompt()
        return attached, prior or prompt

    # 4) 직전 사용자 질문으로 다시 집계
    prior = _prior_user_analysis_prompt()
    if prior:
        grouped = build_groupby_aggregate_table(
            source,
            prior,
            use_budget_profile=_use_budget_profile(),
        )
        if grouped is not None:
            return grouped[0], prior
        contextual = build_context_aggregate_table(
            source,
            prior,
            context_label=context_label,
        )
        if contextual is not None:
            return contextual[0], prior

    return None, prompt


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
        st.session_state.selected_df = None
        st.session_state.analysis_filter_df = None
        st.session_state.analysis_context_label = None
        st.session_state.last_filter_summary = ""
        st.session_state.work_target = "원본 df"
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
