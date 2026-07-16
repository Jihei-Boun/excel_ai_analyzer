"""Ollama LLM 설정 (PandasAI)."""

from __future__ import annotations

from typing import Any

import pandas as pd

try:
    from pandasai import SmartDataframe
    from pandasai.llm.local_llm import LocalLLM
except ImportError:  # pragma: no cover
    SmartDataframe = None  # type: ignore[misc, assignment]
    LocalLLM = None  # type: ignore[misc, assignment]

# LLM이 생성하면 안 되는 API / 모듈 안내 (프롬프트에 포함)
_SAFE_CODE_RULES = (
    "코드 작성 규칙:\n"
    "- pandas DataFrame API만 사용하세요 (loc, iloc, query, str.contains, "
    "groupby, sum, mean, sort_values, nlargest 등).\n"
    "- pandas.compat, os, io, open, sys, subprocess, chr, b64decode, "
    "eval, exec, __import__ 를 절대 사용하지 마세요.\n"
    "- 추가 import를 하지 마세요. 이미 주어진 dfs[0] / df만 사용하세요.\n"
    "- 파일 읽기/쓰기, 네트워크 접근을 하지 마세요.\n"
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


def create_smart_dataframe(
    df: pd.DataFrame,
    *,
    base_url: str,
    model: str,
) -> Any:
    """PandasAI SmartDataframe을 생성한다."""
    if SmartDataframe is None:
        raise ImportError(
            "pandasai가 설치되어 있지 않습니다. "
            "pip install -r requirements.txt 를 실행하세요."
        )

    llm = create_llm(base_url, model)
    return SmartDataframe(
        df,
        config={
            "llm": llm,
            "enable_cache": False,
            "save_charts": False,
            "save_logs": False,
            "verbose": False,
            "max_retries": 1,
            "use_error_correction_framework": True,
        },
    )


def chat(
    df: pd.DataFrame,
    prompt: str,
    *,
    base_url: str,
    model: str,
    output_type: str | None = None,
) -> tuple[Any, str]:
    """PandasAI로 질의를 실행하고 (결과, 요약)을 반환한다."""
    prepared = _prepare_df_for_ai(df)
    safe_prompt = f"{_SAFE_CODE_RULES}\n{prompt}"
    sdf = create_smart_dataframe(prepared, base_url=base_url, model=model)
    kwargs: dict[str, Any] = {}
    if output_type:
        kwargs["output_type"] = output_type

    try:
        raw = sdf.chat(safe_prompt, **kwargs)
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc)) from exc

    result = _unwrap_result(raw)
    summary = _build_summary(result, getattr(sdf, "last_code_executed", None))
    return result, summary


def _prepare_df_for_ai(df: pd.DataFrame) -> pd.DataFrame:
    """PandasAI에 넣기 전 인덱스/타입을 정리한다."""
    out = df.copy().reset_index(drop=True)
    # string dtype을 object로 맞춰 LLM 생성 코드 호환성 향상
    for col in out.columns:
        if str(out[col].dtype) == "string":
            out[col] = out[col].fillna("").astype(object)
    return out


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
    return f"PandasAI 실행 실패: {text}"


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
