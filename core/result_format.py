"""분석 결과 후처리·리스트 표시 판별 — 하위 모듈 재수출."""

from __future__ import annotations

from core.list_display import (
    ListDisplayResult,
    enrich_for_grouped_list,
    exclude_aggregate_rows,
    expects_list_display,
    to_list_display,
)
from core.result_order import (
    restore_source_row_order,
    wants_explicit_sort,
)

__all__ = [
    "ListDisplayResult",
    "expects_list_display",
    "wants_explicit_sort",
    "restore_source_row_order",
    "exclude_aggregate_rows",
    "to_list_display",
    "enrich_for_grouped_list",
]
