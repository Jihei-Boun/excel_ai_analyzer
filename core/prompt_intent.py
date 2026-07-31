"""프롬프트 의도·출력 타입·집계 연산 판별."""

from __future__ import annotations

import re

from core.text_normalize import normalize_text

_COMPLEX_KEYWORDS = (
    "상위",
    "하위",
    "합계",
    "평균",
    "최대",
    "최소",
    "정렬",
    "그룹",
    "피벗",
    "통계",
    "비율",
    "비교",
    "상관",
    "추이",
    "차트",
    "그래프",
    "sum",
    "mean",
    "avg",
    "max",
    "min",
    "sort",
    "group",
    "pivot",
    "chart",
    "plot",
)

_LIST_REQUEST_KEYWORDS = (
    "리스트",
    "목록",
    "나열",
    "뽑아",
    "list",
)

TABLE_AND_CHART_KEYWORDS = (
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

_CHART_KEYWORDS = (
    "차트",
    "그래프",
    "막대그래프",
    "원그래프",
    "시각화",
    "chart",
    "plot",
    "graph",
    "bar chart",
)

_AGGREGATE_OPS = (
    ("평균", "mean"),
    ("mean", "mean"),
    ("avg", "mean"),
    ("최댓값", "max"),
    ("최대", "max"),
    ("max", "max"),
    ("최솟값", "min"),
    ("최소", "min"),
    ("min", "min"),
    ("총합", "sum"),
    ("총 합", "sum"),
    ("종합", "sum"),
    ("합계", "sum"),
    ("합산", "sum"),
    ("집계", "sum"),
    ("sum", "sum"),
    ("total", "sum"),
)


def expects_plot(prompt: str) -> bool:
    """차트·그래프 시각화 요청인지 판별한다."""
    lowered = prompt.lower()
    return any(keyword in lowered for keyword in _CHART_KEYWORDS)


def is_pivot_request(prompt: str) -> bool:
    """피벗·교차 집계 요청인지 판별한다."""
    if not prompt:
        return False
    lowered = prompt.lower()
    normalized = normalize_text(prompt)
    keywords = ("피벗", "pivot", "교차", "크로스탭", "crosstab")
    return any(k in lowered or k in normalized for k in keywords)


def resolve_output_type(prompt: str) -> str | None:
    """요청에 맞는 PandasAI output_type을 고른다. 차트는 plot을 우선한다."""
    if expects_plot(prompt):
        return "plot"
    if expects_dataframe(prompt):
        return "dataframe"
    return None


def expects_dataframe(prompt: str) -> bool:
    """표 형태 결과를 요구하는 표현인지 판별한다."""
    if is_pivot_request(prompt):
        return True
    lowered = prompt.lower()
    table_keywords = (
        "리스트",
        "목록",
        "표",
        "보여",
        "출력",
        "조회",
        "검색",
        "필터",
        "추출",
        "행",
        "열",
        "상위",
        "하위",
        "정렬",
        "list",
        "table",
        "show",
        "filter",
        "rows",
        "columns",
    )
    return any(keyword in lowered for keyword in table_keywords)


def is_list_request(prompt: str) -> bool:
    """데이터 값 리스트 요청인지. '컬럼 목록' 등 스키마 문구는 제외."""
    from core.schema_compare import is_schema_request

    if is_schema_request(prompt):
        return False
    lowered = prompt.lower()
    compact = normalize_text(prompt)
    # '컬럼목록'/'열목록'만 있고 다른 리스트 동사가 없으면 스키마로 본다
    if ("컬럼목록" in compact or "열목록" in compact) and not any(
        k in compact for k in ("리스트", "뽑아", "나열", "list")
    ):
        return False
    return any(keyword in lowered for keyword in _LIST_REQUEST_KEYWORDS)


def wants_table_and_chart(prompt: str) -> bool:
    """표(리스트)와 차트 둘 다 요청했는지."""
    lowered = prompt.lower()
    return any(kw in lowered for kw in TABLE_AND_CHART_KEYWORDS)


def is_complex_analysis(prompt: str) -> bool:
    """집계·순위·시각화처럼 단순 값 필터로 해결할 수 없는 요청인지 판별한다."""
    if detect_aggregate_op(prompt) is not None:
        return True
    if is_pivot_request(prompt):
        return True
    lowered = prompt.lower()
    return any(keyword in lowered for keyword in _COMPLEX_KEYWORDS)


def _match_aggregate_op(prompt: str) -> str | None:
    """프롬프트에서 집계 연산 종류를 찾는다 (차트 요청 포함)."""
    lowered = prompt.lower()
    normalized = normalize_text(prompt)
    # '실행예산의 합', '집행계 합'처럼 '합' 단독 표현도 합계로 인식한다.
    # (종합/통합처럼 단어 내부의 '합'은 제외)
    if re.search(r"(?:^|[\s(])합(?:계|산)?(?:을|를|은|는|이|가)?(?:$|[\s)])", prompt):
        return "sum"
    if re.search(r"의\s*합(?:계|산)?(?:을|를|은|는|이|가)?", prompt):
        return "sum"
    if re.search(r"별\s*합(?:계|산)?(?:을|를|은|는|이|가)?", prompt):
        return "sum"
    if re.search(r"(?:^|[^가-힣a-z0-9])합(?:계|산)?(?:을|를|은|는|이|가)$", normalized):
        return "sum"

    for keyword, op in sorted(_AGGREGATE_OPS, key=lambda item: len(item[0]), reverse=True):
        key_l = keyword.lower()
        key_n = normalize_text(keyword)
        if key_l in lowered or key_n in normalized:
            return op
    return None


def detect_aggregate_op(prompt: str) -> str | None:
    """프롬프트에서 집계 연산 종류를 찾는다. 없으면 None.

    차트 요청은 집계 단축 경로가 아닌 시각화 경로로 보내기 위해 None을 반환한다.
    """
    if expects_plot(prompt):
        return None
    return _match_aggregate_op(prompt)


# Back-compat underscore aliases
_wants_table_and_chart = wants_table_and_chart
_expects_plot = expects_plot
_resolve_output_type = resolve_output_type
_expects_dataframe = expects_dataframe
_is_list_request = is_list_request
_is_complex_analysis = is_complex_analysis
_is_pivot_request = is_pivot_request
