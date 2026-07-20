"""Ollama LLM 설정 (PandasAI)."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

try:
    from pandasai import SmartDataframe, SmartDatalake
    from pandasai.connectors import PandasConnector
    from pandasai.llm.local_llm import LocalLLM
except ImportError:  # pragma: no cover
    SmartDataframe = None  # type: ignore[misc, assignment]
    SmartDatalake = None  # type: ignore[misc, assignment]
    PandasConnector = None  # type: ignore[misc, assignment]
    LocalLLM = None  # type: ignore[misc, assignment]

_SAFE_CODE_RULES = (
    "코드 작성 규칙:\n"
    "- 이미 제공된 DataFrame과 pandas DataFrame API만 사용하세요.\n"
    "- 별도 모듈을 불러오지 말고 메모리 안의 데이터만 분석하세요.\n"
    "- 문자열 검색은 컬럼을 astype(str)로 변환한 뒤 수행하세요.\n"
    "- 데이터에 있는 분류명과 정확히 일치하는 요청은 해당 컬럼의 동등 비교를 사용하세요.\n"
    "- result의 type은 dataframe, number, string, plot 중 하나만 사용하세요.\n"
    "- 목록 결과는 Python list가 아니라 dataframe type의 DataFrame 또는 Series로 반환하세요.\n"
)

_TOTAL_LABEL_RE = re.compile(
    r"^(?:소\s*계|합\s*계|총\s*계|sub\s*total|grand\s*total|total)$",
    flags=re.IGNORECASE,
)


def create_llm(base_url: str, model: str) -> Any:
    """Ollama용 PandasAI LocalLLM 인스턴스를 생성한다."""
    if LocalLLM is None:
        raise ImportError(
            "pandasai가 설치되어 있지 않습니다. "
            "pip install -r requirements.txt 를 실행하세요."
        )

    api_base = base_url.rstrip("/")
    if not api_base.endswith("/v1"):
        api_base = f"{api_base}/v1"

    return LocalLLM(api_base=api_base, model=model)


def _pandasai_config(base_url: str, model: str) -> dict[str, Any]:
    return {
        "llm": create_llm(base_url, model),
        "enable_cache": False,
        "save_charts": False,
        "save_logs": False,
        "verbose": False,
        "max_retries": 1,
        "use_error_correction_framework": True,
    }


def create_smart_dataframe(
    df: pd.DataFrame,
    *,
    base_url: str,
    model: str,
    name: str | None = None,
) -> Any:
    """PandasAI SmartDataframe을 생성한다."""
    if SmartDataframe is None:
        raise ImportError(
            "pandasai가 설치되어 있지 않습니다. "
            "pip install -r requirements.txt 를 실행하세요."
        )

    return SmartDataframe(
        df,
        name=name,
        config=_pandasai_config(base_url, model),
    )


def create_smart_datalake(
    named_dfs: list[tuple[str, pd.DataFrame]],
    *,
    base_url: str,
    model: str,
) -> Any:
    """여러 DataFrame을 PandasAI SmartDatalake로 묶는다."""
    if SmartDatalake is None or PandasConnector is None:
        raise ImportError(
            "pandasai가 설치되어 있지 않습니다. "
            "pip install -r requirements.txt 를 실행하세요."
        )
    if len(named_dfs) < 2:
        raise ValueError("다중 파일 분석에는 파일 2개 이상이 필요합니다.")

    connectors = []
    used_names: set[str] = set()
    for index, (file_name, df) in enumerate(named_dfs):
        table_name = _unique_table_name(file_name, index, used_names)
        used_names.add(table_name)
        connectors.append(
            PandasConnector(
                {"original_df": prepare_dataframe_for_ai(df)},
                name=table_name,
                description=f"엑셀 파일: {file_name}",
            )
        )

    return SmartDatalake(connectors, config=_pandasai_config(base_url, model))


def chat(
    df: pd.DataFrame,
    prompt: str,
    *,
    base_url: str,
    model: str,
    output_type: str | None = None,
) -> tuple[Any, str]:
    """PandasAI로 질의를 실행하고 (결과, 요약)을 반환한다."""
    prepared = prepare_dataframe_for_ai(df)
    safe_prompt = f"{_SAFE_CODE_RULES}\n{prompt}"
    sdf = create_smart_dataframe(prepared, base_url=base_url, model=model)
    return _run_chat_session(sdf, safe_prompt, output_type=output_type)


def chat_multi(
    named_dfs: list[tuple[str, pd.DataFrame]],
    prompt: str,
    *,
    base_url: str,
    model: str,
    output_type: str | None = None,
) -> tuple[Any, str]:
    """여러 파일을 SmartDatalake로 동시에 분석한다."""
    inventory = _multi_file_inventory(named_dfs)
    safe_prompt = (
        f"{_SAFE_CODE_RULES}\n"
        "여러 DataFrame(dfs[0], dfs[1], …)이 제공됩니다. "
        "파일 이름과 테이블 이름을 참고해 비교·병합·교차 집계를 수행하세요.\n"
        f"{inventory}\n"
        f"{prompt}"
    )
    lake = create_smart_datalake(named_dfs, base_url=base_url, model=model)
    return _run_chat_session(lake, safe_prompt, output_type=output_type)


def _run_chat_session(
    agent: Any,
    safe_prompt: str,
    *,
    output_type: str | None = None,
) -> tuple[Any, str]:
    kwargs: dict[str, Any] = {}
    if output_type:
        kwargs["output_type"] = output_type

    try:
        raw = agent.chat(safe_prompt, **kwargs)
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc)) from exc

    result = _unwrap_result(raw)
    if _is_retryable_response_error(result):
        if output_type == "dataframe":
            result_contract = (
                '마지막 줄에서 반드시 result = {"type": "dataframe", '
                '"value": result_df} 형식으로 반환하세요.\n'
                "result_df는 pandas DataFrame 또는 Series여야 합니다."
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
        result = _unwrap_result(raw)

    _raise_if_error_response(result)
    summary = _build_summary(result, getattr(agent, "last_code_executed", None))
    return result, summary


def _unique_table_name(file_name: str, index: int, used: set[str]) -> str:
    stem = re.sub(r"\.[^.]+$", "", file_name)
    safe = re.sub(r"[^0-9A-Za-z_]", "_", stem).strip("_") or f"file_{index}"
    if safe[0].isdigit():
        safe = f"t_{safe}"
    candidate = safe
    suffix = 1
    while candidate in used:
        candidate = f"{safe}_{suffix}"
        suffix += 1
    return candidate


def _multi_file_inventory(named_dfs: list[tuple[str, pd.DataFrame]]) -> str:
    lines = ["제공된 파일 목록:"]
    used: set[str] = set()
    for index, (file_name, df) in enumerate(named_dfs):
        table = _unique_table_name(file_name, index, used)
        used.add(table)
        cols = ", ".join(str(c) for c in list(df.columns)[:12])
        more = "" if len(df.columns) <= 12 else f" 외 {len(df.columns) - 12}개"
        lines.append(
            f"- dfs[{index}] 테이블명={table} / 파일={file_name} / "
            f"{len(df):,}행 × {len(df.columns)}열 / 컬럼: {cols}{more}"
        )
    return "\n".join(lines)


def prepare_dataframe_for_ai(df: pd.DataFrame) -> pd.DataFrame:
    """PandasAI에 넣기 전 인덱스/타입을 정리한다."""
    out = df.copy().reset_index(drop=True)
    for col in out.columns:
        if _is_hierarchical_column(out[col]):
            out[col] = _fill_hierarchical_labels(out[col])

    # string dtype을 object로 맞춰 LLM 생성 코드 호환성 향상
    for col in out.columns:
        if str(out[col].dtype) == "string":
            out[col] = out[col].fillna("").astype(object)
    return out


def _is_hierarchical_column(series: pd.Series) -> bool:
    """`그룹명 → 빈 행들 → 소계/합계` 패턴인지 확인한다."""
    if pd.api.types.is_numeric_dtype(series):
        return False

    values = series.tolist()
    for index, value in enumerate(values):
        if _is_blank(value) or _is_total_label(value):
            continue

        has_blank_detail = False
        for following in values[index + 1 :]:
            if _is_blank(following):
                has_blank_detail = True
                continue
            if _is_total_label(following):
                if has_blank_detail:
                    return True
                break
            break
    return False


def _fill_hierarchical_labels(series: pd.Series) -> pd.Series:
    filled: list[object] = []
    current_group: object | None = None

    for value in series.tolist():
        if _is_total_label(value):
            filled.append(value)
            current_group = None
        elif _is_blank(value):
            filled.append(current_group if current_group is not None else value)
        else:
            filled.append(value)
            current_group = value

    return pd.Series(filled, index=series.index, dtype="string")


def is_total_label(value: object) -> bool:
    """합계·소계·총계 등 집계 행 라벨인지 확인한다."""
    if _is_blank(value):
        return False
    return bool(_TOTAL_LABEL_RE.fullmatch(str(value).strip()))


def _is_total_label(value: object) -> bool:
    return is_total_label(value)


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _friendly_error(exc: BaseException) -> str:
    text = str(exc)
    lowered = text.lower()
    if "malicious" in lowered or "shouldn't use" in lowered:
        return (
            "AI가 안전하지 않은 코드를 생성해 실행이 차단되었습니다. "
            "요청을 더 구체적으로 바꿔 다시 시도해 주세요. "
            f"(상세: {text})"
        )
    if "compat" in lowered:
        return (
            "AI가 현재 pandas 버전과 맞지 않는 코드를 생성했습니다. "
            "요청을 다시 보내거나 조건을 단순화해 주세요. "
            f"(상세: {text})"
        )
    if "must match with type list" in lowered:
        return (
            "AI가 목록 결과를 PandasAI가 지원하지 않는 형식으로 생성했습니다. "
            "목록을 표 형식으로 다시 요청해 주세요."
        )
    return f"PandasAI 실행 실패: {text}"


def _raise_if_error_response(result: Any) -> None:
    """PandasAI가 예외 대신 반환한 표준 오류 문장을 감지한다."""
    if not isinstance(result, str):
        return
    lowered = result.lower()
    is_pandasai_error = (
        "unfortunately" in lowered
        and "not able to" in lowered
        and "following error" in lowered
    )
    if not is_pandasai_error:
        return
    if "execute or access system resources" in lowered:
        raise RuntimeError(
            "PandasAI 보안 검사에서 요청이 차단되었습니다. "
            "분석 요청을 데이터 조건 중심으로 다시 입력해 주세요."
        )
    if "must match with type list" in lowered:
        raise RuntimeError(
            "AI가 목록을 지원되지 않는 형식으로 반환했습니다. "
            "목록 결과는 표(DataFrame)로 생성해야 합니다."
        )
    if "result must be in the format of dictionary" in lowered:
        raise RuntimeError(
            "AI가 분석 결과를 PandasAI 규격에 맞게 반환하지 못했습니다. "
            "같은 요청을 다시 시도해 주세요."
        )
    raise RuntimeError(f"PandasAI가 요청을 처리하지 못했습니다: {result}")


def _is_retryable_response_error(result: Any) -> bool:
    if not isinstance(result, str):
        return False
    lowered = result.lower()
    return (
        "result must be in the format of dictionary" in lowered
        or "must match with type list" in lowered
        or "no code found in the response" in lowered
        or "can only use .str accessor with string values" in lowered
    )


def _unwrap_result(raw: Any) -> Any:
    if isinstance(raw, dict) and "value" in raw:
        return raw["value"]
    if SmartDataframe is not None and isinstance(raw, SmartDataframe):
        try:
            return raw.dataframe
        except Exception:
            return raw
    return raw


def _build_summary(result: Any, code: str | None) -> str:
    if isinstance(result, pd.DataFrame):
        return f"PandasAI 결과: {len(result):,}행 × {len(result.columns)}열"
    if _is_number(result):
        return f"PandasAI 결과: {float(result):,.0f}"
    if code:
        first_line = next((line.strip() for line in code.splitlines() if line.strip()), "")
        if first_line:
            return f"PandasAI 실행 완료: {first_line[:80]}"
    return "PandasAI 분석을 완료했습니다."


def _is_number(value: Any) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    try:
        import numpy as np

        return isinstance(value, (np.integer, np.floating))
    except ImportError:
        return False
