"""The calculation line restates the plan and reaches the chat and headless output."""

from __future__ import annotations

import pandas as pd

from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.plan_explain import explain_plan
from core.application.headless import _allowlisted_meta
from core.routing.route_helpers import _merge_analysis_meta
from core.schema.row_classify import ROW_TYPE_COL

_COLUMNS = ["지점", "구분", "1분기", "2분기", "3분기", "4분기", "수량", "단가", "금액"]


def _explain(steps: list[dict], row_types: list[str] | None = None) -> str:
    plan = analysis_plan_from_dict({"steps": steps}, available_columns=_COLUMNS)
    source = pd.DataFrame({ROW_TYPE_COL: row_types}) if row_types is not None else None
    return explain_plan(plan, source)


def test_row_range_sum_lists_every_operand() -> None:
    line = _explain([
        {"op": "filter_rows", "include_row_types": ["detail"]},
        {"op": "derive_column", "name": "합", "expr": {"sum": ["2분기", "3분기", "4분기"]}},
        {"op": "select_columns", "columns": ["지점", "합"]},
    ], row_types=["detail"] * 4)
    assert line == "행마다 합 = 2분기 + 3분기 + 4분기"


def test_dropped_row_types_are_named_only_when_present_with_counts() -> None:
    steps = [
        {"op": "filter_rows", "include_row_types": ["detail"]},
        {"op": "filter_rows", "include_row_types": ["detail"]},
        {"op": "aggregate", "group_by": ["지점"], "metrics": [{"column": "금액", "fn": "sum"}]},
    ]
    line = _explain(steps, row_types=["detail", "subtotal", "detail", "subtotal", "total"])
    assert line == "소계 2행·합계 1행 제외 → 지점별 금액 합계"


def test_long_value_lists_are_shortened() -> None:
    line = _explain([
        {"op": "filter_rows", "column_filters": [
            {"column": "구분", "values": ["가", "나", "다", "라", "마"]}]},
    ])
    assert line == "구분이 가·나 외 3개인 행만"


def test_aggregates_of_separate_columns_are_visible_as_such() -> None:
    """Summing quantity and price separately is shown plainly, so a wrong total cost is visible."""
    line = _explain([
        {"op": "aggregate", "group_by": ["지점"], "metrics": [
            {"column": "수량", "fn": "sum"}, {"column": "단가", "fn": "sum"}]},
    ])
    assert line == "지점별 수량 합계, 단가 합계"


def test_value_filter_and_overall_total() -> None:
    line = _explain([
        {"op": "filter_rows", "include_row_types": ["detail", "subtotal", "total", "footer"],
         "column_filters": [{"column": "구분", "values": ["집행"]}]},
        {"op": "aggregate", "group_by": [], "metrics": [{"column": "금액", "fn": "sum"}]},
    ])
    assert line == "구분이 집행인 행만 → 전체 행의 금액 합계"


def test_ratio_sort_and_limit() -> None:
    line = _explain([
        {"op": "aggregate", "group_by": ["지점"], "metrics": [
            {"column": "금액", "fn": "sum"}, {"column": "수량", "fn": "sum"}]},
        {"op": "derive_column", "name": "단가평균", "expr": {"ratio": ["금액", "수량"]}},
        {"op": "sort", "by": ["단가평균"], "ascending": False},
        {"op": "limit", "n": 1},
    ])
    assert line.endswith("행마다 단가평균 = 금액 ÷ 수량 → 단가평균 큰 순 정렬 → 위에서 1개")


def test_explanation_passes_routing_and_headless_allowlist() -> None:
    meta = _merge_analysis_meta({}, {"plan_explanation": "지점별 금액 합계", "retry_log": []})
    assert meta == {"plan_explanation": "지점별 금액 합계"}
    assert _allowlisted_meta(meta) == {"plan_explanation": "지점별 금액 합계"}


def test_column_pick_and_transpose_are_described() -> None:
    """A plan that only picks columns and transposes used to get no line at all."""
    plan = analysis_plan_from_dict(
        {"steps": [{"op": "select_columns", "columns": ["지점", "1분기", "2분기", "3분기", "4분기"]}],
         "transpose": True},
        available_columns=_COLUMNS,
    )
    assert plan.transpose
    assert explain_plan(plan) == "지점·1분기·2분기·3분기 외 1개 열만 → 행과 열 바꾸기"


def test_column_pick_is_not_repeated_after_a_calculation() -> None:
    line = _explain([
        {"op": "aggregate", "group_by": ["지점"], "metrics": [{"column": "금액", "fn": "sum"}]},
        {"op": "select_columns", "columns": ["지점", "금액"]},
        {"op": "drop_columns", "columns": ["단가"]},
    ])
    assert line == "지점별 금액 합계 → 단가 열 빼기"
