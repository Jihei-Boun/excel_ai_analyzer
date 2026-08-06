"""PandasAI result unwrap / errors / summary helpers."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from core.chart_utils import materialize_chart
from core.constants import CODE_SUMMARY_LINE_LEN

try:
    from pandasai import SmartDataframe
except ImportError:  # pragma: no cover
    SmartDataframe = None  # type: ignore[misc, assignment]


def _friendly_error(exc: BaseException) -> str:
    from core.profile_loader import guardrail_hints_for

    text = str(exc)
    hints = guardrail_hints_for()
    code_col = hints.get("code_col", "코드")
    name_col = hints.get("name_col", "명칭")
    group_col = hints.get("group_col", "분류")

    # 이미 가공된 RuntimeError 메시지는 그대로 둔다.
    if isinstance(exc, RuntimeError) and (
        text.startswith("PandasAI")
        or text.startswith("AI가")
        or text.startswith(f"{code_col} 코드")
        or text.startswith("비용명 코드")
        or text.startswith("숫자형 열")
    ):
        return text

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
    if _looks_like_code_key_error(exc, text):
        return (
            f"{code_col} 코드 값(예: 121, 121.0)을 컬럼명처럼 조회하다 실패했습니다. "
            f"피벗·교차 축에는 코드 열 대신 명칭 열(예: {name_col})을 사용하세요. "
            f"(상세: {text})"
        )
    if "can only use .str accessor" in lowered:
        return (
            "숫자형 열에 .str을 사용했습니다. "
            "코드 열은 분석용으로 문자열 변환되어 있으니 astype(str) 후 사용하거나 "
            "명칭 열을 쓰세요. "
            f"(상세: {text})"
        )
    if (
        "inappropriate 'type'" in lowered
        or "actual 'none'" in lowered
        or "value none seems to be inappropriate" in lowered
    ):
        return (
            "AI가 표(DataFrame) 대신 빈 결과(None)를 반환했습니다. "
            f"피벗 시 분석용으로 forward-fill된 {group_col}를 index로 쓰고, "
            "소계/합계 행을 제외한 뒤 non-null DataFrame을 반환하도록 "
            "다시 시도해 주세요. "
            f"(상세: {text})"
        )
    return f"PandasAI 실행 실패: {text}"


def _looks_like_code_key_error(exc: BaseException, text: str) -> bool:
    if isinstance(exc, KeyError):
        key = exc.args[0] if exc.args else text
        return _is_code_like_key(key)
    # PandasAI가 문자열로만 넘기는 경우: "'121.0'" / "121.0"
    stripped = text.strip().strip("'\"")
    return _is_code_like_key(stripped)


def _is_code_like_key(value: object) -> bool:
    text = str(value).strip().strip("'\"")
    if not text:
        return False
    try:
        number = float(text)
    except ValueError:
        return False
    return abs(number) < 100_000


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
    # "following error: '121.0'" 형태
    match = re.search(
        r"following error:\s*(.+)$",
        str(result),
        flags=re.IGNORECASE | re.DOTALL,
    )
    detail = match.group(1).strip() if match else str(result)
    if _looks_like_code_key_error(KeyError(detail.strip().strip("'\"")), detail):
        raise RuntimeError(_friendly_error(KeyError(detail.strip().strip("'\""))))
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
        or "inappropriate 'type'" in lowered
        or "actual 'none'" in lowered
        or "value none seems to be inappropriate" in lowered
    )


def _unwrap_result(raw: Any) -> tuple[Any, dict[str, Any]]:
    meta: dict[str, Any] = {}
    if isinstance(raw, dict) and "value" in raw:
        if str(raw.get("type", "")).lower() == "plot":
            chart_path = materialize_chart(raw.get("value"))
            if chart_path:
                meta["chart_path"] = chart_path
        return raw["value"], meta
    if SmartDataframe is not None and isinstance(raw, SmartDataframe):
        try:
            return raw.dataframe, meta
        except Exception:
            return raw, meta
    chart_path = materialize_chart(raw)
    if chart_path:
        meta["chart_path"] = chart_path
    return raw, meta


def _coerce_chart_path(value: Any) -> str | None:
    """하위 호환용 — materialize_chart로 위임한다."""
    return materialize_chart(value)


def _build_summary(
    result: Any,
    code: str | None,
    meta: dict[str, Any] | None = None,
) -> str:
    meta = meta or {}
    if meta.get("chart_path"):
        return "차트 결과를 생성했습니다."
    if isinstance(result, pd.DataFrame):
        return f"PandasAI 결과: {len(result):,}행 × {len(result.columns)}열"
    if _is_number(result):
        return f"PandasAI 결과: {float(result):,.0f}"
    if isinstance(result, str):
        text = result.strip()
        if text:
            return text
    if code:
        first_line = next((line.strip() for line in code.splitlines() if line.strip()), "")
        if first_line:
            return f"PandasAI 실행 완료: {first_line[:CODE_SUMMARY_LINE_LEN]}"
    return "PandasAI 분석을 완료했습니다."


def _is_number(value: Any) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    try:
        import numpy as np

        return isinstance(value, (np.integer, np.floating))
    except ImportError:
        return False
