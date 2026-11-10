"""‘A 대비 B ~률’ 요청에서 계획이 빠뜨린 비율 계산을 채우는 보정."""

from __future__ import annotations

import pandas as pd

from core.analysis.analysis_executor import execute_analysis_plan
from core.analysis.analysis_plan_sanitize import (
    add_ratio_from_prompt,
    percent_column_labels,
    prefer_percent_for_rate,
)
from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.analysis_plan_validate import validate_analysis_plan
from core.schema.row_classify import classify_rows


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "부서": ["영업", "영업", "연구", "기획"],
            "예산_합계": [100.0, 300.0, 200.0, 0.0],
            "집행_합계": [50.0, 150.0, 20.0, 0.0],
        }
    )


def _sum_plan(metrics: list[str]):
    return analysis_plan_from_dict(
        {
            "operation": "aggregate",
            "group_by": ["부서"],
            "metrics": [{"column": m, "fn": "sum"} for m in metrics],
        },
        available_columns=list(_df().columns),
    )


def _fix(plan, prompt: str):
    return prefer_percent_for_rate(add_ratio_from_prompt(plan, prompt), prompt)


def test_missing_rate_is_added_as_percent_after_aggregate() -> None:
    prompt = "부서별 예산 합계 대비 집행 합계 집행률을 구해줘"
    plan = _fix(_sum_plan(["예산_합계", "집행_합계"]), prompt)
    df = classify_rows(_df())
    assert validate_analysis_plan(plan, df, user_prompt=prompt).ok
    result, _ = execute_analysis_plan(df, plan)
    assert list(result.columns) == ["부서", "예산_합계", "집행_합계", "집행률"]
    rates = dict(zip(result["부서"], result["집행률"]))
    assert rates["영업"] == 50.0
    assert rates["연구"] == 10.0
    assert pd.isna(rates["기획"])  # 분모 0
    assert percent_column_labels(plan) == {"집행률": "집행률(%)"}


def test_metric_order_in_plan_does_not_flip_the_ratio() -> None:
    prompt = "부서별 예산 대비 집행 집행률"
    plan = _fix(_sum_plan(["집행_합계", "예산_합계"]), prompt)
    derive = [s for s in plan.steps if s.op == "derive_column"]
    assert derive[0].payload["expr"] == {"percent_ratio": ["집행_합계", "예산_합계"]}


def test_plain_ratio_word_names_the_column() -> None:
    plan = _fix(_sum_plan(["예산_합계", "집행_합계"]), "부서별 예산 합계 대비 집행 합계 비율")
    derive = [s for s in plan.steps if s.op == "derive_column"]
    assert derive[0].payload["name"] == "비율"
    # ‘~율’로 끝나는 이름은 기존 규칙대로 백분율이 된다.
    assert derive[0].payload["expr"] == {"percent_ratio": ["집행_합계", "예산_합계"]}


def test_no_change_without_rate_word_or_unclear_sides() -> None:
    for prompt in (
        "부서별 예산 합계 대비 집행 합계를 보여줘",  # 비교 표, 비율 요청 아님
        "부서별 집행률을 구해줘",  # ‘대비’가 없어 분모를 모름
        "부서별 예산 합계와 집행 합계 대비 집행률",  # 앞쪽에 열이 둘
    ):
        plan = _fix(_sum_plan(["예산_합계", "집행_합계"]), prompt)
        assert [s.op for s in plan.steps if s.op == "derive_column"] == [], prompt


def test_existing_ratio_step_is_left_alone() -> None:
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "aggregate", "group_by": ["부서"], "metrics": [
                    {"column": "예산_합계", "fn": "sum"}, {"column": "집행_합계", "fn": "sum"}]},
                {"op": "ratio_of_aggregates", "name": "rate",
                 "numerator": "집행_합계", "denominator": "예산_합계"},
            ]
        },
        available_columns=list(_df().columns),
    )
    before = [s.to_dict() for s in plan.steps]
    plan = _fix(plan, "부서별 예산 합계 대비 집행 합계 집행률")
    assert [s.to_dict() for s in plan.steps] == before
