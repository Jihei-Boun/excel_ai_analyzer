"""Phase 5 — Legacy / lightweight fallback 분류.

Production single-file 우선순위
--------------------------------
System/Data command
→ AnalysisPlan Pipeline
→ Lightweight deterministic retrieval (value_match / list_seed)
→ legacy_simple_groupby_fallback (단순 X별 Y만)
→ PandasAI

| 기능 | 분류 | Production 역할 |
|------|------|----------------|
| build_groupby_aggregate_table | B | Planner exhausted 후 단순 groupby만 |
| build_context_aggregate_table | A→B* | analytical path 제거; chart helper만 잔존 |
| try_condition_row_filter | A | analytical path 제거 (Planner find_items/filter) |
| value_match | C | exact retrieval fallback |
| list_seed | C | 단순 목록 retrieval |
| chart fallback | D | chart-only display; 분석+차트는 Planner 우선 |

A=제거(주경로), B=fallback-only, C=retrieval, D=display
* context aggregate는 route_helpers 차트 해석용으로만 유지.
"""

from __future__ import annotations

import pandas as pd

from core.aggregates import build_groupby_aggregate_table

LEGACY_FALLBACK_CLASSIFICATION: dict[str, str] = {
    "build_groupby_aggregate_table": "B",
    "build_context_aggregate_table": "A_chart_helper_only",
    "try_condition_row_filter": "A",
    "value_match": "C",
    "list_seed": "C",
    "chart_fallback": "D",
}


def try_legacy_simple_groupby_fallback(
    df: pd.DataFrame,
    prompt: str,
    *,
    profile_name: str | None = None,
) -> tuple[pd.DataFrame, str] | None:
    """Planner exhausted 후 단순 'X별 Y 합계/평균' deterministic fallback.

    Router에서 직접 호출하지 말 것. 의미 비교·비율·순위는 Planner 책임.
    """
    return build_groupby_aggregate_table(
        df,
        prompt,
        profile_name=profile_name,
    )
