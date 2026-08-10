"""Phase 12: retry diversity / operation family signature."""

from __future__ import annotations

import pandas as pd

from core.analysis.analysis_pipeline import try_analysis_pipeline
from core.analysis.analysis_plan_contract import (
    operation_family_signature,
    repeated_operation_family_feedback,
)
from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.analysis_plan_validate import (
    format_plan_validation_feedback,
    validate_analysis_plan,
)
from core.schema.row_classify import classify_rows


def test_operation_family_mean_vs_column_comparison() -> None:
    mean_plan = {
        "steps": [
            {"op": "filter_vs_mean", "column": "current_value", "relation": "below"},
        ]
    }
    col_plan = {
        "steps": [
            {
                "op": "filter_rows",
                "numeric_filters": [
                    {
                        "left_column": "current_value",
                        "op": "<",
                        "right_column": "threshold_value",
                    }
                ],
            }
        ]
    }
    assert operation_family_signature(mean_plan) == "mean_based_filter"
    assert operation_family_signature(col_plan) == "column_comparison_filter"
    assert operation_family_signature(mean_plan) != operation_family_signature(col_plan)


def test_repeated_family_feedback_does_not_prescribe_answer_ops() -> None:
    lines = repeated_operation_family_feedback(
        "mean_based_filter", retry_mode="regenerate"
    )
    joined = "\n".join(lines)
    assert "mean-based filtering" in joined
    assert "materially different" in joined.lower() or "Avoid repeating" in joined
    assert "left_column" not in joined
    assert "filter_rows" not in joined


def test_retry_diversity_mock_pipeline_recovers_on_third_attempt() -> None:
    """Attempt0/1: filter_vs_mean → fail/repeat family; Attempt2: col-vs-col → ok."""
    df = pd.DataFrame(
        {
            "item": ["A", "B", "C"],
            "current_value": [3, 10, 1],
            "threshold_value": [5, 5, 5],
        }
    )
    calls = {"n": 0}

    def chat_json(_prompt: str, **_kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            return {
                "steps": [
                    {
                        "op": "filter_vs_mean",
                        "column": "current_value",
                        "relation": "below",
                    }
                ],
                "interpret": False,
            }
        return {
            "steps": [
                {
                    "op": "filter_rows",
                    "numeric_filters": [
                        {
                            "left_column": "current_value",
                            "op": "<",
                            "right_column": "threshold_value",
                        }
                    ],
                },
                {
                    "op": "select_columns",
                    "columns": ["item", "current_value", "threshold_value"],
                },
            ],
            "interpret": False,
        }

    exhaust: dict = {}
    result = try_analysis_pipeline(
        "threshold보다 낮은 항목을 알려줘",
        df,
        base_url="http://localhost:11434",
        model="mock",
        chat_json_fn=chat_json,
        chat_text_fn=lambda *_a, **_k: "",
        exhaust_meta=exhaust,
        max_retries=3,
    )
    assert result is not None
    assert not result.dataframe.empty
    assert set(result.dataframe["item"]) == {"A", "C"}
    families = [
        r.get("operation_family")
        for r in (result.meta.get("retry_log") or [])
        if r.get("operation_family")
    ]
    assert "mean_based_filter" in families
    assert any(r.get("same_operation_family_repeat") for r in result.meta.get("retry_log") or [])
    assert result.meta.get("same_operation_family_repeat", 0) >= 1


def test_column_vs_column_regression_generic_fixture() -> None:
    """Phase 10 style col-vs-col plan remains valid (no inventory-specific assert)."""
    df = pd.DataFrame(
        {
            "item": ["x", "y", "z"],
            "current_value": [1, 9, 4],
            "threshold_value": [5, 5, 5],
        }
    )
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "filter_rows",
                    "numeric_filters": [
                        {
                            "left_column": "current_value",
                            "op": "<",
                            "right_column": "threshold_value",
                        }
                    ],
                },
                {"op": "select_columns", "columns": ["item", "current_value"]},
            ]
        },
        available_columns=list(df.columns),
    )
    report = validate_analysis_plan(
        plan,
        classify_rows(df, dimension_columns=["item"]),
        user_prompt="threshold보다 낮은 항목",
    )
    assert report.ok, [i.message for i in report.errors]
    assert operation_family_signature(plan.to_dict()) == "column_comparison_filter"


def test_feedback_on_repeated_family_mentions_rejected_family() -> None:
    df = pd.DataFrame({"current_value": [1, 2], "threshold_value": [5, 5]})
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "filter_vs_mean", "column": "current_value", "relation": "below"}
            ]
        },
        available_columns=list(df.columns),
    )
    report = validate_analysis_plan(
        plan,
        classify_rows(df),
        user_prompt="threshold보다 낮은 항목",
    )
    assert not report.ok
    fb = format_plan_validation_feedback(
        report,
        previous_plan=plan.to_dict(),
        df=df,
        attempt=1,
        retry_mode="regenerate",
        operation_family="mean_based_filter",
        repeated_operation_family=True,
    )
    text = "\n".join(fb)
    assert "Previous rejected family" in text or "mean-based filtering" in text
    assert "left_column=" not in text
