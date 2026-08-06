"""Ollama LLM 설정 (PandasAI)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.display.chart_utils import materialize_chart
from core.pai.pandasai_frame import (
    _TOTAL_LABEL_RE,
    _fill_hierarchical_labels,
    _format_code_cell,
    _is_blank,
    _is_hierarchical_column,
    _is_total_label,
    _stringify_code_metric_columns,
    exclude_total_rows,
    is_total_label,
    prepare_dataframe_for_ai,
    sum_metric_excluding_totals,
)
from core.pai.pandasai_result import (
    _build_summary,
    _coerce_chart_path,
    _friendly_error,
    _is_code_like_key,
    _is_number,
    _is_retryable_response_error,
    _looks_like_code_key_error,
    _raise_if_error_response,
    _unwrap_result,
)
from core.pai.pandasai_setup import (
    _CHARTS_DIR,
    _IMAGE_SUFFIXES,
    _SAFE_CODE_RULES,
    LocalLLM,
    PandasConnector,
    SmartDataframe,
    SmartDatalake,
    _multi_file_inventory,
    _pandasai_config,
    _unique_table_name,
    create_llm,
    create_smart_dataframe,
    create_smart_datalake,
)

__all__ = [
    "LocalLLM",
    "PandasConnector",
    "SmartDataframe",
    "SmartDatalake",
    "_CHARTS_DIR",
    "_IMAGE_SUFFIXES",
    "_SAFE_CODE_RULES",
    "_TOTAL_LABEL_RE",
    "_build_summary",
    "_coerce_chart_path",
    "_fill_hierarchical_labels",
    "_format_code_cell",
    "_friendly_error",
    "_is_blank",
    "_is_code_like_key",
    "_is_hierarchical_column",
    "_is_number",
    "_is_retryable_response_error",
    "_is_total_label",
    "_looks_like_code_key_error",
    "_multi_file_inventory",
    "_pandasai_config",
    "_raise_if_error_response",
    "_run_chat_session",
    "_stringify_code_metric_columns",
    "_unique_table_name",
    "_unwrap_result",
    "chat",
    "chat_multi",
    "create_llm",
    "create_smart_dataframe",
    "create_smart_datalake",
    "exclude_total_rows",
    "is_total_label",
    "prepare_dataframe_for_ai",
    "sum_metric_excluding_totals",
]


def chat(
    df: pd.DataFrame,
    prompt: str,
    *,
    base_url: str,
    model: str,
    output_type: str | None = None,
) -> tuple[Any, str, dict[str, Any]]:
    """PandasAI로 질의를 실행하고 (결과, 요약, 메타)을 반환한다.

    원본 df는 변경하지 않고 분석용 복사본만 사용한다.
    """
    from core.code_guardrails import (
        build_regeneration_prompt,
        extract_aggregation_meta,
        format_aggregation_notice,
        inspect_generated_code,
        validate_pandasai_result,
    )
    from core.schema.schema_hints import format_schema_hints_for_prompt, prepare_analysis_frame

    raw_df = df
    analysis_df = prepare_analysis_frame(raw_df)
    schema_block = format_schema_hints_for_prompt(raw_df)
    safe_prompt = f"{_SAFE_CODE_RULES}\n"
    if schema_block:
        safe_prompt += f"{schema_block}\n"
    safe_prompt += prompt

    sdf = create_smart_dataframe(analysis_df, base_url=base_url, model=model)
    try:
        result, summary, meta = _run_chat_session(
            sdf, safe_prompt, output_type=output_type
        )
        first_error: str | None = None
    except RuntimeError as exc:
        result, summary, meta = None, "", {}
        first_error = str(exc)
        code = getattr(sdf, "last_code_executed", None)
        if code:
            meta["code"] = code

    code = meta.get("code") or getattr(sdf, "last_code_executed", None)
    issues = inspect_generated_code(
        code,
        available_columns=[str(c) for c in analysis_df.columns],
    )
    if first_error:
        issues.append(first_error)
        lowered_error = first_error.lower()
        from core.profile_loader import guardrail_hints_for

        hints = guardrail_hints_for()
        code_col = hints.get("code_col", "코드")
        name_col = hints.get("name_col", "명칭")
        group_col = hints.get("group_col", "분류")
        if "duplicate entries" in lowered_error or "reshape" in lowered_error:
            issues.append(
                "중복 키로 pivot/unstack가 실패했습니다. "
                "소계·합계 행 제외와 pivot_table(aggfunc=...) 사용을 검토하세요."
            )
        if _looks_like_code_key_error(Exception(first_error), first_error) or (
            "121" in first_error and ("key" in lowered_error or first_error.strip().startswith("'"))
        ):
            issues.append(
                "숫자 코드(예: 121.0)를 컬럼/키로 조회하지 마세요. "
                f"'{code_col}' 요청이면 코드 열 대신 명칭 열({name_col})을 "
                "pivot_table columns/index에 사용하세요."
            )
        if "can only use .str accessor" in lowered_error:
            issues.append(
                "숫자형 열에 .str을 쓰지 마세요. 명칭 열을 쓰거나 "
                "astype(str) 후 문자열 연산을 하세요."
            )
        if (
            "inappropriate 'type'" in lowered_error
            or "actual 'none'" in lowered_error
            or "value none" in lowered_error
        ):
            issues.append(
                "result는 반드시 non-null DataFrame이어야 합니다. "
                "예: result = {\"type\": \"dataframe\", \"value\": pivot_df}. "
                f"{group_col} 빈칸은 분석용 데이터에서 이미 위 값으로 채워져 있으니 "
                "그대로 index로 사용하고, 소계/합계 행만 제외하세요. "
                "value=None 또는 빈 결과는 반환하지 마세요."
            )
    if result is not None:
        hard_result, soft_result = validate_pandasai_result(
            result,
            source_row_count=len(analysis_df),
            code=code if isinstance(code, str) else None,
            user_prompt=prompt,
        )
        issues.extend(hard_result)
        if soft_result:
            meta["result_warnings"] = soft_result

    if issues:
        retry_prompt = build_regeneration_prompt(safe_prompt, issues)
        result, summary, meta = _run_chat_session(
            sdf,
            retry_prompt,
            output_type=output_type,
        )
        code = meta.get("code") or getattr(sdf, "last_code_executed", None)
        retry_issues = inspect_generated_code(
            code,
            available_columns=[str(c) for c in analysis_df.columns],
        )
        hard_retry, soft_retry = validate_pandasai_result(
            result,
            source_row_count=len(analysis_df),
            code=code if isinstance(code, str) else None,
            user_prompt=prompt,
        )
        retry_issues.extend(hard_retry)
        if soft_retry:
            meta["result_warnings"] = soft_retry
        if retry_issues:
            meta["guardrail_issues"] = retry_issues

    agg_meta = extract_aggregation_meta(code if isinstance(code, str) else None)
    if agg_meta:
        meta["aggregation"] = agg_meta
        notice = format_aggregation_notice(agg_meta)
        if notice:
            summary = f"{summary} · {notice}"
            meta["aggregation_notice"] = notice
    return result, summary, meta


def chat_multi(
    named_dfs: list[tuple[str, pd.DataFrame]],
    prompt: str,
    *,
    base_url: str,
    model: str,
    output_type: str | None = None,
) -> tuple[Any, str, dict[str, Any]]:
    """여러 파일을 SmartDatalake로 동시에 분석한다."""
    from core.code_guardrails import (
        build_regeneration_prompt,
        extract_aggregation_meta,
        format_aggregation_notice,
        inspect_generated_code,
        validate_pandasai_result,
    )
    from core.schema.schema_hints import format_schema_hints_for_prompt, prepare_analysis_frame

    prepared_named = [
        (name, prepare_analysis_frame(frame)) for name, frame in named_dfs
    ]
    inventory = _multi_file_inventory(prepared_named)
    hint_blocks = []
    for name, raw_frame in named_dfs:
        block = format_schema_hints_for_prompt(raw_frame)
        if block:
            hint_blocks.append(f"[{name}]\n{block}")
    schema_block = "\n".join(hint_blocks)

    safe_prompt = (
        f"{_SAFE_CODE_RULES}\n"
        "여러 DataFrame(dfs[0], dfs[1], …)이 제공됩니다. "
        "파일 이름과 테이블 이름을 참고해 비교·병합·교차 집계를 수행하세요.\n"
        f"{inventory}\n"
    )
    if schema_block:
        safe_prompt += f"{schema_block}\n"
    safe_prompt += prompt

    lake = create_smart_datalake(prepared_named, base_url=base_url, model=model)
    result, summary, meta = _run_chat_session(
        lake, safe_prompt, output_type=output_type
    )

    code = meta.get("code") or getattr(lake, "last_code_executed", None)
    all_columns = [str(c) for _, frame in prepared_named for c in frame.columns]
    total_rows = sum(len(frame) for _, frame in prepared_named)
    issues = inspect_generated_code(code, available_columns=all_columns)
    hard_result, soft_result = validate_pandasai_result(
        result,
        source_row_count=total_rows,
        code=code if isinstance(code, str) else None,
        user_prompt=prompt,
    )
    issues.extend(hard_result)
    if soft_result:
        meta["result_warnings"] = soft_result
    if issues:
        retry_prompt = build_regeneration_prompt(safe_prompt, issues)
        result, summary, meta = _run_chat_session(
            lake, retry_prompt, output_type=output_type
        )
        code = meta.get("code") or getattr(lake, "last_code_executed", None)
        retry_issues = inspect_generated_code(code, available_columns=all_columns)
        hard_retry, soft_retry = validate_pandasai_result(
            result,
            source_row_count=total_rows,
            code=code if isinstance(code, str) else None,
            user_prompt=prompt,
        )
        retry_issues.extend(hard_retry)
        if soft_retry:
            meta["result_warnings"] = soft_retry
        if retry_issues:
            meta["guardrail_issues"] = retry_issues

    agg_meta = extract_aggregation_meta(code if isinstance(code, str) else None)
    if agg_meta:
        meta["aggregation"] = agg_meta
        notice = format_aggregation_notice(agg_meta)
        if notice:
            summary = f"{summary} · {notice}"
            meta["aggregation_notice"] = notice
    return result, summary, meta


def _run_chat_session(
    agent: Any,
    safe_prompt: str,
    *,
    output_type: str | None = None,
) -> tuple[Any, str, dict[str, Any]]:
    kwargs: dict[str, Any] = {}
    if output_type:
        kwargs["output_type"] = output_type

    try:
        raw = agent.chat(safe_prompt, **kwargs)
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc)) from exc

    result, meta = _unwrap_result(raw)
    if _is_retryable_response_error(result):
        if output_type == "dataframe":
            result_contract = (
                '마지막 줄에서 반드시 result = {"type": "dataframe", '
                '"value": result_df} 형식으로 반환하세요.\n'
                "result_df는 pandas DataFrame 또는 Series여야 합니다."
            )
        elif output_type == "plot":
            result_contract = (
                '마지막 줄에서 반드시 result = {"type": "plot", '
                '"value": chart_path} 형식으로 반환하세요.\n'
                "chart_path는 저장된 차트 이미지 파일 경로여야 합니다."
            )
        else:
            result_contract = (
                "마지막 줄의 result는 type과 value를 가진 dictionary여야 합니다.\n"
                "type은 dataframe, number, string, plot 중 결과에 맞는 하나를 사용하세요."
            )
        retry_prompt = (
            f"{safe_prompt}\n"
            "이전 응답을 실행할 수 없었습니다. 코드를 다시 작성하세요.\n"
            "설명 문장은 쓰지 말고 실행 가능한 Python 코드 블록 하나만 반환하세요.\n"
            f"{result_contract}"
        )
        try:
            raw = agent.chat(retry_prompt, **kwargs)
        except Exception as exc:
            raise RuntimeError(_friendly_error(exc)) from exc
        result, meta = _unwrap_result(raw)

    _raise_if_error_response(result)
    code = getattr(agent, "last_code_executed", None)
    if code:
        meta["code"] = code

    # ResponseParser가 value만 반환하므로 last_result의 plot dict도 확인한다.
    last_result = getattr(agent, "last_result", None)
    if not meta.get("chart_path") and isinstance(last_result, dict):
        if str(last_result.get("type", "")).lower() == "plot":
            chart_path = materialize_chart(last_result.get("value"))
            if chart_path:
                meta["chart_path"] = chart_path

    chart_path = meta.get("chart_path") or materialize_chart(result)
    if chart_path:
        meta["chart_path"] = chart_path
    summary = _build_summary(result, code, meta)
    return result, summary, meta
