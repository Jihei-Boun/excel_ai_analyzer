"""프롬프트 라우팅·결과 후처리 (Streamlit 없음).

구현은 route_types / route_helpers / route_single / route_multi 에 두고
여기서는 기존 import 경로 호환을 위해 재수출한다.
"""

from __future__ import annotations

from core.route_helpers import (
    _attach_filter_summary_meta,
    _context_updates_from_filter,
    _merge_analysis_meta,
    needs_chart_context,
    postprocess_table_result,
    resolve_chart_table,
    resolve_multi_aggregate_source,
)
from core.route_multi import route_multi_prompt
from core.route_single import route_single_prompt
from core.route_types import SingleRouteOutcome

__all__ = [
    "SingleRouteOutcome",
    "needs_chart_context",
    "resolve_chart_table",
    "postprocess_table_result",
    "_context_updates_from_filter",
    "_merge_analysis_meta",
    "_attach_filter_summary_meta",
    "resolve_multi_aggregate_source",
    "route_single_prompt",
    "route_multi_prompt",
]
