"""Phase 7 regression: aggregate fns, quality routing, column-vs-column filter."""

from __future__ import annotations

import pandas as pd
import pytest

from core.analysis.analysis_executor import execute_analysis_plan
from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.analysis_plan_validate import validate_analysis_plan
from core.analysis.ops_aggregate import aggregate_groups
from core.routing.prompt_intent import is_system_data_command
from core.schema.quality import is_quality_request
from core.schema.row_classify import classify_rows
from core.pai.pandasai_frame import prepare_dataframe_for_ai


def _dept_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "부서": ["A", "A", "B", "B", "C"],
            "연봉": [100, 200, 300, 100, 50],
            "점수": [10, 20, 30, 40, 5],
        }
    )


def test_aggregate_sum_mean_count_min_max_median() -> None:
    df = classify_rows(_dept_df(), dimension_columns=["부서"])
    specs = [
        ("sum", {"A": 300.0, "B": 400.0, "C": 50.0}),
        ("mean", {"A": 150.0, "B": 200.0, "C": 50.0}),
        ("count", {"A": 2.0, "B": 2.0, "C": 1.0}),
        ("min", {"A": 100.0, "B": 100.0, "C": 50.0}),
        ("max", {"A": 200.0, "B": 300.0, "C": 50.0}),
        ("median", {"A": 150.0, "B": 200.0, "C": 50.0}),
    ]
    for fn, expected in specs:
        result, meta = aggregate_groups(
            df,
            group_by=["부서"],
            metrics=[{"column": "연봉", "fn": fn}],
            prefer_subtotals=False,
        )
        mapping = {
            str(r["부서"]): float(r["연봉"]) for _, r in result.iterrows()
        }
        for key, want in expected.items():
            assert abs(mapping[key] - want) < 1e-6, (fn, key, mapping)
        assert meta["aggregations"]["연봉"] == fn


def test_unsupported_aggregation_validation_error() -> None:
    df = _dept_df()
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [{"column": "연봉", "fn": "mode"}],
                }
            ]
        },
        available_columns=list(df.columns),
    )
    report = validate_analysis_plan(plan, classify_rows(df, dimension_columns=["부서"]))
    assert not report.ok
    assert any(i.code == "unsupported_aggregation" for i in report.errors)


def test_quality_request_true_cases() -> None:
    for prompt in (
        "데이터에 문제가 있는지 알려줘",
        "품질을 확인해줘",
        "이상한 데이터가 있는지 봐줘",
        "결측 문제를 확인해줘",
        "결측/중복 문제를 확인해줘",
        "품질 진단해줘",
    ):
        assert is_quality_request(prompt) is True, prompt
        assert is_system_data_command(prompt) is True, prompt


def test_quality_request_false_positives() -> None:
    for prompt in (
        "취소 주문 제외 후 매출 계산해줘",
        "문제상품 매출 합계",
        "문제코드별 합계를 알려줘",
        "주문제외 후 카테고리별 매출",
    ):
        assert is_quality_request(prompt) is False, prompt
        assert is_system_data_command(prompt) is False, prompt


def test_column_vs_column_numeric_filter() -> None:
    df = pd.DataFrame(
        {
            "상품코드": ["P1", "P2", "P3"],
            "재고수량": [12, 5, 80],
            "안전재고": [20, 15, 30],
        }
    )
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "filter_rows",
                    "numeric_filters": [
                        {
                            "left_column": "재고수량",
                            "op": "lt",
                            "right_column": "안전재고",
                        }
                    ],
                }
            ]
        },
        available_columns=list(df.columns),
    )
    result, _ = execute_analysis_plan(classify_rows(df), plan)
    assert set(result["상품코드"]) == {"P1", "P2"}


def test_prepare_dataframe_coerces_comma_numbers_and_headers() -> None:
    dirty = pd.DataFrame(
        {
            " 상품 명 ": ["연필", "노트", "합계"],
            "수 량": ["1,000", "200", "1,200"],
            "매출액": ["10,000", "4,000", "14,000"],
            "Unnamed: 3": [None, None, None],
        }
    )
    prepared = prepare_dataframe_for_ai(dirty)
    assert "상품_명" in prepared.columns or "상품명" in [
        c.replace("_", "") for c in prepared.columns
    ]
    # empty unnamed dropped
    assert not any(str(c).startswith("Unnamed") for c in prepared.columns)
    qty_col = [c for c in prepared.columns if "수" in c][0]
    assert pd.api.types.is_numeric_dtype(prepared[qty_col])
    assert float(prepared[qty_col].iloc[0]) == 1000.0


def test_find_items_value_as_column_name_becomes_col_vs_col() -> None:
    """LLM이 value에 컬럼명을 넣어도 column-vs-column으로 해석."""
    df = pd.DataFrame(
        {
            "상품코드": ["P1", "P2", "P3"],
            "재고수량": [12, 5, 80],
            "안전재고": [20, 15, 30],
        }
    )
    plan = analysis_plan_from_dict(
        {
            "operation": "find_items",
            "numeric_filters": [
                {"column": "재고수량", "op": "lt", "value": "안전재고"},
            ],
            "output_columns": ["상품코드", "재고수량", "안전재고"],
            "interpret": False,
        },
        available_columns=list(df.columns),
    )
    result, _ = execute_analysis_plan(classify_rows(df), plan)
    assert set(result["상품코드"]) == {"P1", "P2"}


def test_high_level_aggregate_compile() -> None:
    df = pd.DataFrame(
        {
            "상품_명": ["연필", "노트", "연필"],
            "매출액": [1000, 2000, 500],
        }
    )
    plan = analysis_plan_from_dict(
        {
            "operation": "aggregate",
            "group_by": ["상품_명"],
            "metrics": [{"column": "매출액", "fn": "sum"}],
            "interpret": False,
        },
        available_columns=list(df.columns),
    )
    result, meta = execute_analysis_plan(classify_rows(df), plan)
    mapping = {str(r["상품_명"]): float(r["매출액"]) for _, r in result.iterrows()}
    assert mapping["연필"] == 1500.0
    assert mapping["노트"] == 2000.0
    assert "aggregate" in {s.op for s in plan.steps}
    from core.analysis.analysis_result_validate import validate_analysis_result

    df = pd.DataFrame(
        {
            "상품": ["A", "B"],
            "당년도매출": [100, 200],
            "누적매출": [500, 800],
            "목표매출": [120, 180],
            "지역": ["서울", "부산"],
        }
    )
    plan = analysis_plan_from_dict(
        {
            "operation": "group_comparison",
            "group_column": "지역",
            "groups": ["서울", "부산"],
            "numerator": "당년도매출",
            "denominator": "목표매출",
            "rate_name": "달성률",
        },
        available_columns=list(df.columns),
    )
    result, meta = execute_analysis_plan(classify_rows(df), plan)
    report = validate_analysis_result(
        result,
        plan,
        source_df=df,
        exec_meta=meta,
        profile_name="generic",
        user_prompt="매출 실적을 비교해줘",
    )
    assert report.ok
    assert any(i.code == "semantic_role_mismatch" for i in report.warnings)
