"""aggregate_missing_metric: a later select_columns may leave an aggregate metric out."""

from __future__ import annotations

import pandas as pd

from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.analysis_result_validate import validate_analysis_result


def _codes(plan_steps: list[dict], result: pd.DataFrame) -> set[str]:
    plan = analysis_plan_from_dict({"steps": plan_steps}, available_columns=["노선", "운행", "승객"])
    report = validate_analysis_result(result, plan)
    return {i.code for i in report.issues if i.level == "error"}


_AGGREGATE = {"op": "aggregate", "group_by": ["노선"], "metrics": [
    {"column": "운행", "fn": "sum"}, {"column": "승객", "fn": "sum"}]}


def test_metric_dropped_by_a_later_select_is_not_missing() -> None:
    steps = [
        _AGGREGATE,
        {"op": "derive_column", "name": "회당승객", "expr": {"ratio": ["승객", "운행"]}},
        {"op": "select_columns", "columns": ["노선", "회당승객"]},
    ]
    result = pd.DataFrame({"노선": ["가", "나"], "회당승객": [12.5, 7.0]})
    assert "aggregate_missing_metric" not in _codes(steps, result)


def test_metric_missing_without_a_select_is_still_an_error() -> None:
    result = pd.DataFrame({"노선": ["가", "나"], "운행": [4, 6]})
    assert "aggregate_missing_metric" in _codes([_AGGREGATE], result)
