"""생성 코드 정적 검사·결과 검증 — 자동 치환 없이 재생성 지침만 만든다."""

from __future__ import annotations

import ast
import re
from typing import Any

import pandas as pd

from core.column_match import _is_amount_metric_column

_PIVOT_CALL_RE = re.compile(r"\.pivot\s*\(")
_PIVOT_TABLE_RE = re.compile(r"\.pivot_table\s*\(")
_OPEN_RE = re.compile(r"\bopen\s*\(")
_IMPORT_RE = re.compile(r"^\s*(import|from)\s+", re.MULTILINE)
_GROUPBY_RE = re.compile(r"\.groupby\s*\((?P<args>[^)]*)\)", re.DOTALL)
_AGGfunc_RE = re.compile(r"aggfunc\s*=\s*['\"](?P<fn>[^'\"]+)['\"]")
_AGG_METHOD_RE = re.compile(r"\.(sum|mean|min|max|count|median)\s*\(")


def inspect_generated_code(
    code: str | None,
    *,
    available_columns: list[str] | None = None,
) -> list[str]:
    """위험·오류 가능 패턴을 찾아 재생성용 이슈 목록을 반환한다."""
    if not code or not str(code).strip():
        return []

    text = str(code)
    issues: list[str] = []

    if _IMPORT_RE.search(text):
        issues.append("별도 모듈 import가 있습니다. 제공된 DataFrame과 pandas API만 사용하세요.")
    if _OPEN_RE.search(text):
        issues.append("파일 open() 접근이 있습니다. 메모리의 DataFrame만 분석하세요.")

    if _PIVOT_CALL_RE.search(text) and not _PIVOT_TABLE_RE.search(text):
        # unique pivot은 허용. reshape 실패 시에만 chat()이 재생성 이슈로 올린다.
        pass
    elif _PIVOT_TABLE_RE.search(text) and "aggfunc" not in text:
        issues.append(
            "pivot_table에 aggfunc가 없습니다. 집계 함수를 명시하세요. "
            "금액 합산이 분명할 때만 sum을 사용하세요."
        )

    if available_columns is not None:
        missing = _referenced_missing_columns(text, available_columns)
        if missing:
            issues.append(
                "존재하지 않는 컬럼을 참조했습니다: "
                + ", ".join(missing)
                + f". 사용 가능 컬럼: {', '.join(str(c) for c in available_columns)}"
            )

    if "Index contains duplicate entries" in text:
        issues.append(
            "중복 인덱스 reshape 오류가 있었습니다. "
            "소계/합계 행 제외 후 pivot_table(aggfunc=...)을 검토하세요."
        )

    return issues


def validate_pandasai_result(
    result: Any,
    *,
    source_row_count: int | None = None,
    code: str | None = None,
    user_prompt: str | None = None,
) -> tuple[list[str], list[str]]:
    """PandasAI 실행 결과 이상 징후를 (재생성용 이슈, 경고)로 반환한다.

    analysis_validate.validate_analysis_result(계획 실행 검증)과 구분한다.
    빈 결과는 유효한 필터일 수 있어 경고만 남긴다.
    피벗이 거의 대각선 sparse이면 축 교체 재생성을 요청한다.
    """
    hard: list[str] = []
    soft: list[str] = []
    if result is None:
        return hard, soft

    if isinstance(result, pd.DataFrame):
        if result.empty:
            soft.append("결과가 빈 DataFrame입니다.")
        elif result.isna().all().all():
            hard.append("결과 값이 모두 NaN입니다. 컬럼 선택과 집계를 재검토하세요.")
        if (
            source_row_count is not None
            and source_row_count > 0
            and len(result) > source_row_count * 5
            and len(result) > 100
        ):
            hard.append(
                f"결과 행 수({len(result):,})가 원본({source_row_count:,})보다 "
                "비정상적으로 큽니다. 조인/교차 조건을 재검토하세요."
            )

        pivot_like = _code_looks_like_pivot(code) or _frame_looks_like_crosstab(result)
        if pivot_like and has_broken_pivot_row_axis(result):
            axis_hint = _pivot_axis_order_hint(user_prompt)
            hard.append(
                "피벗 행 축(왼쪽 열)이 비어 있거나 분류명 대신 숫자/결측으로 보입니다. "
                "계층형 분류는 이미 분석용 복사본에서 forward-fill되어 있으니 "
                "그 열을 index로 쓰세요. 소계·합계·총계·내부흡수액·외부유출액 행은 "
                "피벗 전에 제외하세요. reset_index() 후 행 축 컬럼명을 "
                "비목분류 등 의미 있는 이름으로 두세요. "
                f"{axis_hint}"
                "명칭 열(예: 비용명_2)을 columns로 사용하세요."
            )
        if pivot_like and is_near_diagonal_sparse_pivot(result):
            axis_hint = _pivot_axis_order_hint(user_prompt)
            hard.append(
                "피벗 결과가 거의 대각선입니다(대부분의 행에 값이 1개만 있고 "
                "나머지는 결측/None). index와 columns 축이 뒤바뀌었을 가능성이 큽니다. "
                f"{axis_hint}"
                "pivot_table로 다시 작성하고, 금액 피벗이면 fill_value=0을 검토하세요. "
                "결과를 자동 전치(transpose)하지 말고 코드를 재작성하세요."
            )
    return hard, soft


def has_broken_pivot_row_axis(df: pd.DataFrame) -> bool:
    """피벗 결과의 행 라벨 열이 비어 있거나 금액처럼 보이는지 판별한다."""
    if df is None or df.empty or df.shape[1] < 2 or df.shape[0] < 3:
        return False

    first_name = str(df.columns[0]).strip()
    first = df.iloc[:, 0]
    blank_ratio = float((~first.map(_cell_has_value)).mean())
    numeric = pd.to_numeric(first, errors="coerce")
    amount_like = numeric.notna() & (numeric.abs() >= 10_000)
    amount_ratio = float(amount_like.mean())
    label_hits = float(
        first.map(
            lambda v: bool(
                _cell_has_value(v)
                and not (
                    pd.notna(pd.to_numeric(v, errors="coerce"))
                    and abs(float(pd.to_numeric(v, errors="coerce"))) >= 10_000
                )
            )
        ).mean()
    )

    unnamed = (
        not first_name
        or first_name.lower() in {"index", "level_0", "unnamed: 0", "none"}
        or first_name.startswith("Unnamed")
    )

    # 행 라벨이 거의 없고 wide 숫자열만 남은 경우
    if blank_ratio >= 0.4 and label_hits <= 0.4:
        return True
    # 행 라벨 자리에 큰 금액이 섞인 경우 (footer 집행계 등이 인덱스로 들어간 패턴)
    if amount_ratio >= 0.15 and label_hits <= 0.5:
        return True
    # 헤더가 비어 있고 라벨 품질이 낮은 경우
    if unnamed and (blank_ratio >= 0.25 or amount_ratio >= 0.1):
        return True
    return False


def is_near_diagonal_sparse_pivot(df: pd.DataFrame) -> bool:
    """행마다 값이 거의 1개뿐인 sparse wide 표인지 판별한다."""
    if df is None or df.empty or df.shape[0] < 3 or df.shape[1] < 2:
        return False

    value_cols = _value_columns_for_sparsity(df)
    if len(value_cols) < 2:
        return False

    matrix = df[value_cols]
    present = matrix.map(_cell_has_value)
    if present.to_numpy().size == 0:
        return False

    null_ratio = 1.0 - float(present.to_numpy().mean())
    per_row = present.sum(axis=1)
    mostly_single = float((per_row <= 1).mean())

    # 대각선형: 결측이 많고, 행 대부분이 값 0~1개
    if null_ratio < 0.45 or mostly_single < 0.7:
        return False
    # 값이 아예 없으면 다른 검증(전체 NaN)에 맡긴다
    if int(per_row.sum()) == 0:
        return False
    return True


def _value_columns_for_sparsity(df: pd.DataFrame) -> list[str]:
    """라벨 후보 첫 열을 제외하고 값 열을 고른다."""
    cols = [str(c) for c in df.columns]
    if not cols:
        return []

    def _numeric_hit_ratio(series: pd.Series) -> float:
        coerced = pd.to_numeric(series, errors="coerce")
        present = series.map(_cell_has_value)
        if not present.any():
            return 0.0
        return float(coerced.notna().sum() / max(int(present.sum()), 1))

    # 첫 열이 문자열 라벨이고 나머지에 숫자가 있으면 첫 열 제외
    first = df.iloc[:, 0]
    rest = cols[1:]
    if rest and _numeric_hit_ratio(first) < 0.3:
        rest_hits = [_numeric_hit_ratio(df[c]) for c in rest]
        if any(hit >= 0.2 for hit in rest_hits):
            return [c for c, hit in zip(rest, rest_hits) if hit >= 0.1]

    return [c for c in cols if _numeric_hit_ratio(df[c]) >= 0.1]


def _cell_has_value(value: object) -> bool:
    if value is None:
        return False
    try:
        if bool(pd.isna(value)):
            return False
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "null", "<na>"}:
        return False
    return True


def _code_looks_like_pivot(code: str | None) -> bool:
    if not code:
        return False
    text = str(code)
    return bool(_PIVOT_CALL_RE.search(text) or _PIVOT_TABLE_RE.search(text))


def _frame_looks_like_crosstab(df: pd.DataFrame) -> bool:
    """코드가 없어도 wide 숫자표처럼 보이면 피벗 검증 대상으로 본다."""
    if df is None or df.empty:
        return False
    value_cols = _value_columns_for_sparsity(df)
    return len(value_cols) >= 3 and len(df) >= 3


def _pivot_axis_order_hint(user_prompt: str | None) -> str:
    if not user_prompt:
        return (
            "요청에서 먼저 나온 분류 축을 행(index), 다음에 나온 축을 열(columns)로 두세요. "
        )
    # 'A와 B', 'A과 B', 'A/B' 정도만 가볍게 힌트 (조사 제외)
    match = re.search(
        r"([0-9A-Za-z가-힣_]+?)\s*(?:와|과|/|,)\s*([0-9A-Za-z가-힣_]+?)(?:을|를|이|가|은|는|로|으로)?(?:\s|$)",
        user_prompt,
    )
    if not match:
        return (
            "요청에서 먼저 나온 분류 축을 행(index), 다음에 나온 축을 열(columns)로 두세요. "
        )
    left, right = match.group(1), match.group(2)
    return (
        f"요청 언급 순서를 따라 index='{left}'(또는 대응 명칭 열), "
        f"columns='{right}'(또는 대응 명칭 열)로 두세요. "
    )

def extract_aggregation_meta(code: str | None) -> dict[str, Any]:
    """생성 코드에서 집계 키·함수를 추출한다 (표시용)."""
    if not code:
        return {}

    text = str(code)
    meta: dict[str, Any] = {}

    aggfunc_match = _AGGfunc_RE.search(text)
    if aggfunc_match:
        meta["aggregation_used"] = aggfunc_match.group("fn")
    else:
        method_match = _AGG_METHOD_RE.search(text)
        if method_match:
            meta["aggregation_used"] = method_match.group(1)

    group_keys: list[str] = []
    for match in _GROUPBY_RE.finditer(text):
        group_keys.extend(_literal_string_list(match.group("args")))
    if group_keys:
        meta["group_keys"] = list(dict.fromkeys(group_keys))

    if _PIVOT_TABLE_RE.search(text) or _PIVOT_CALL_RE.search(text):
        meta["operation"] = "pivot"
    elif "groupby" in text:
        meta["operation"] = "groupby"

    return meta


def format_aggregation_notice(meta: dict[str, Any]) -> str | None:
    """사용자 요약에 붙일 짧은 집계 안내."""
    if not meta:
        return None
    parts: list[str] = []
    keys = meta.get("group_keys") or []
    agg = meta.get("aggregation_used")
    op = meta.get("operation")
    if keys and agg:
        parts.append(f"집계 기준: {', '.join(keys)}별 {agg}")
    elif keys:
        parts.append(f"집계 기준: {', '.join(keys)}")
    elif agg and op == "pivot":
        parts.append(f"피벗 집계: {agg}")
    elif agg:
        parts.append(f"집계 함수: {agg}")
    if op == "pivot" and agg == "sum":
        parts.append("중복 행은 합산 처리했을 수 있습니다.")
    return " · ".join(parts) if parts else None


def build_regeneration_prompt(base_prompt: str, issues: list[str]) -> str:
    """실패 원인·수정 지침을 포함한 재생성 프롬프트."""
    bullet = "\n".join(f"- {issue}" for issue in issues)
    return (
        f"{base_prompt}\n\n"
        "이전 코드에 아래 문제가 있었습니다. 자동 치환하지 말고 코드를 다시 작성하세요.\n"
        f"{bullet}\n"
        "설명 문장 없이 실행 가능한 Python 코드만 반환하세요."
    )


def amount_column_allows_sum(column_name: str) -> bool:
    """자동 sum 허용 여부를 금액 힌트로만 판별한다 (강제 rewrite 아님)."""
    return _is_amount_metric_column(column_name)


def _literal_string_list(args_text: str) -> list[str]:
    text = args_text.strip()
    if not text:
        return []
    try:
        if text.startswith("["):
            value = ast.literal_eval(_first_bracket(text))
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value]
        value = ast.literal_eval(text.split(",")[0].strip())
        if isinstance(value, str):
            return [value]
    except (SyntaxError, ValueError):
        pass
    return re.findall(r"['\"]([^'\"]+)['\"]", text)


def _first_bracket(text: str) -> str:
    start = text.find("[")
    if start < 0:
        return "[]"
    depth = 0
    for index, char in enumerate(text[start:], start=start):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return "[]"


def _referenced_missing_columns(code: str, available: list[str]) -> list[str]:
    available_set = {str(c) for c in available}
    # df['col'] / df["col"] / df[['a','b']]
    refs = re.findall(r"['\"]([^'\"]+)['\"]", code)
    # kwargs / common non-columns 제외
    ignore = {
        "type",
        "value",
        "dataframe",
        "plot",
        "string",
        "number",
        "index",
        "columns",
        "values",
        "aggfunc",
        "sum",
        "mean",
        "min",
        "max",
        "count",
        "how",
        "axis",
    }
    missing = [
        name
        for name in refs
        if name not in available_set and name not in ignore and len(name) >= 2
    ]
    return list(dict.fromkeys(missing))
