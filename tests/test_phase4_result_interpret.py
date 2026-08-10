"""Phase 4: Result Validator contracts · Interpreter 입력 계약."""

from __future__ import annotations

import json

import pandas as pd

from core.analysis.analysis_executor import execute_analysis_plan
from core.analysis.analysis_interpret import (
    _INTERPRETER_HARD_CONSTRAINTS,
    build_interpreter_payload,
    interpret_analysis_result,
)
from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.analysis_result_validate import (
    format_result_validation_feedback,
    validate_analysis_result,
)
from core.schema.row_classify import classify_rows


def _sales() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "부서": ["영업", "연구", "영업", "연구"],
            "상품": ["A", "B", "C", "D"],
            "매출": [100.0, 200.0, 50.0, 80.0],
            "비용": [40.0, 60.0, 20.0, 30.0],
        }
    )


def _run(plan_dict: dict, df: pd.DataFrame | None = None):
    raw = df if df is not None else _sales()
    plan = analysis_plan_from_dict(plan_dict, available_columns=list(raw.columns))
    classified = classify_rows(raw, dimension_columns=["부서", "상품"])
    result, meta = execute_analysis_plan(classified, plan)
    report = validate_analysis_result(
        result, plan, source_df=raw, exec_meta=meta, profile_name="generic"
    )
    return plan, result, meta, report


# ---------------------------------------------------------------------------
# Valid contracts
# ---------------------------------------------------------------------------


def test_result_valid_aggregate() -> None:
    _, result, _, report = _run(
        {
            "steps": [
                {"op": "annotate_row_types"},
                {"op": "filter_rows", "include_row_types": ["detail"]},
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                    "prefer_subtotals": False,
                },
            ]
        }
    )
    assert report.ok, report.summary_text()
    assert "부서" in result.columns
    assert "매출" in result.columns
    assert len(result) >= 1


def test_result_valid_ratio() -> None:
    _, result, meta, report = _run(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [
                        {"column": "매출", "fn": "sum"},
                        {"column": "비용", "fn": "sum"},
                    ],
                    "prefer_subtotals": False,
                },
                {
                    "op": "ratio_of_aggregates",
                    "name": "비율",
                    "numerator": "비용",
                    "denominator": "매출",
                },
            ]
        }
    )
    assert report.ok, report.summary_text()
    assert "비율" in result.columns
    assert not meta.get("denominator_zero")


def test_result_valid_group_comparison() -> None:
    _, result, _, report = _run(
        {
            "operation": "group_comparison",
            "group_column": "부서",
            "groups": ["영업", "연구"],
            "numerator": "비용",
            "denominator": "매출",
            "rate_name": "비율",
            "prefer_subtotals": True,
            "interpret": False,
        }
    )
    assert report.ok, report.summary_text()
    assert "부서" in result.columns


def test_result_valid_correlation() -> None:
    _, _, meta, report = _run(
        {
            "operation": "correlation",
            "x_column": "매출",
            "y_column": "비용",
            "label_column": "상품",
            "interpret": False,
        }
    )
    assert report.ok, report.summary_text()
    corr = meta.get("correlation") or {}
    assert corr.get("pearson_r") is not None or corr.get("spearman_rho") is not None


def test_result_valid_ranking() -> None:
    _, result, _, report = _run(
        {
            "steps": [
                {"op": "annotate_row_types"},
                {"op": "filter_rows", "include_row_types": ["detail"]},
                {"op": "sort", "by": ["매출"], "ascending": [False]},
                {"op": "limit", "n": 2},
            ]
        }
    )
    assert report.ok, report.summary_text()
    assert len(result) == 2
    assert float(result.iloc[0]["매출"]) >= float(result.iloc[1]["매출"])


# ---------------------------------------------------------------------------
# Invalid
# ---------------------------------------------------------------------------


def test_result_invalid_empty() -> None:
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                }
            ]
        },
        available_columns=list(_sales().columns),
    )
    report = validate_analysis_result(pd.DataFrame(), plan)
    assert not report.ok
    assert any(i.code == "empty_dataframe" for i in report.errors)


def test_result_invalid_all_nan() -> None:
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                }
            ]
        },
        available_columns=list(_sales().columns),
    )
    report = validate_analysis_result(
        pd.DataFrame({"부서": ["영업"], "매출": [float("nan")]}), plan
    )
    assert not report.ok
    assert any(i.code == "all_nan_result" for i in report.errors)


def test_result_invalid_inf() -> None:
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                }
            ]
        },
        available_columns=list(_sales().columns),
    )
    report = validate_analysis_result(
        pd.DataFrame({"부서": ["영업"], "매출": [float("inf")]}), plan
    )
    assert not report.ok
    assert any(i.code == "inf_result" for i in report.errors)


def test_result_invalid_missing_group() -> None:
    plan = analysis_plan_from_dict(
        {
            "operation": "group_comparison",
            "group_column": "부서",
            "groups": ["영업", "연구"],
            "numerator": "비용",
            "denominator": "매출",
            "rate_name": "비율",
        },
        available_columns=list(_sales().columns),
    )
    # only one group present
    fake = pd.DataFrame({"부서": ["영업"], "매출": [100.0], "비용": [40.0], "비율": [0.4]})
    report = validate_analysis_result(fake, plan, exec_meta={})
    assert not report.ok
    assert any(i.code == "result_missing_groups" for i in report.errors)


def test_result_invalid_denominator_zero_required_group() -> None:
    df = pd.DataFrame(
        {
            "부서": ["영업", "연구"],
            "매출": [100.0, 0.0],
            "비용": [40.0, 10.0],
        }
    )
    plan, result, meta, report = _run(
        {
            "operation": "group_comparison",
            "group_column": "부서",
            "groups": ["영업", "연구"],
            "numerator": "비용",
            "denominator": "매출",
            "rate_name": "비율",
            "prefer_subtotals": False,
            "interpret": False,
        },
        df=df,
    )
    del plan, result
    # 연구 분모 0 → required group error
    assert meta.get("zero_denominator_groups") or any(
        i.code.startswith("denominator_zero") for i in report.issues
    )
    assert not report.ok or any(
        i.code in {"denominator_zero_required_group", "denominator_zero_runtime"}
        for i in report.errors
    ) or any(i.code == "denominator_zero_optional_group" for i in report.warnings)


def test_result_invalid_correlation_out_of_range() -> None:
    plan = analysis_plan_from_dict(
        {
            "operation": "correlation",
            "x_column": "매출",
            "y_column": "비용",
        },
        available_columns=list(_sales().columns),
    )
    report = validate_analysis_result(
        pd.DataFrame({"상품": ["A"], "매출": [1], "비용": [2]}),
        plan,
        exec_meta={"correlation": {"pearson_r": 1.5}},
    )
    assert not report.ok
    assert any(i.code == "correlation_out_of_range" for i in report.errors)


def test_result_invalid_sort_mismatch() -> None:
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "sort", "by": ["매출"], "ascending": [False]},
                {"op": "limit", "n": 3},
            ]
        },
        available_columns=list(_sales().columns),
    )
    bad = _sales().sort_values("매출", ascending=True).head(3).reset_index(drop=True)
    report = validate_analysis_result(bad, plan)
    assert not report.ok
    assert any(i.code == "sort_mismatch" for i in report.errors)


def test_result_invalid_top_per_group_contract() -> None:
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "top_per_group",
                    "group_column": "부서",
                    "value_column": "매출",
                    "n": 1,
                    "ascending": False,
                }
            ]
        },
        available_columns=list(_sales().columns),
    )
    # same group appears twice → exceeds n
    bad = pd.DataFrame(
        {"부서": ["영업", "영업"], "매출": [100.0, 50.0], "상품": ["A", "C"]}
    )
    report = validate_analysis_result(bad, plan)
    assert not report.ok
    assert any(i.code == "top_per_group_exceeds_n" for i in report.errors)


def test_result_warning_limit_underfill() -> None:
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "sort", "by": ["매출"], "ascending": [False]},
                {"op": "limit", "n": 10},
            ]
        },
        available_columns=list(_sales().columns),
    )
    result = _sales().sort_values("매출", ascending=False).reset_index(drop=True)
    report = validate_analysis_result(result, plan)
    assert report.ok
    assert any(i.code == "limit_underfilled" for i in report.warnings)


def test_result_warning_semantic_role_mismatch() -> None:
    plan = analysis_plan_from_dict(
        {
            "operation": "group_comparison",
            "group_column": "비목분류",
            "groups": ["내부인건비", "연구활동비"],
            "numerator": "당년도집행",
            "denominator": "실행예산_합계",
            "rate_name": "집행률",
        },
        available_columns=[
            "비목분류",
            "당년도집행",
            "실행예산_합계",
            "집행계_합계",
        ],
    )
    result = pd.DataFrame(
        {
            "비목분류": ["내부인건비", "연구활동비"],
            "당년도집행": [10, 20],
            "실행예산_합계": [100, 100],
            "집행률": [0.1, 0.2],
        }
    )
    report = validate_analysis_result(
        result,
        plan,
        exec_meta={},
        profile_name="budget",
    )
    assert report.ok
    assert any(i.code == "semantic_role_mismatch" for i in report.warnings)

    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                }
            ]
        },
        available_columns=list(_sales().columns),
    )
    report = validate_analysis_result(pd.DataFrame(), plan)
    feedback = format_result_validation_feedback(
        report, previous_plan=plan.to_dict(), attempt=1
    )
    text = "\n".join(feedback)
    assert "Failure stage: result_validation" in text
    assert "plan executed" in text.lower() or "produced result is invalid" in text.lower()


# ---------------------------------------------------------------------------
# Interpreter contract
# ---------------------------------------------------------------------------


def test_interpreter_payload_excludes_raw_dataframe() -> None:
    plan, result, meta, report = _run(
        {
            "operation": "group_comparison",
            "group_column": "부서",
            "groups": ["영업", "연구"],
            "numerator": "비용",
            "denominator": "매출",
            "rate_name": "비율",
            "prefer_subtotals": True,
            "interpret": True,
        }
    )
    payload = build_interpreter_payload(
        "영업과 연구를 비교해줘",
        result,
        plan,
        exec_meta=meta,
        validation_warnings=[f"{i.code}: {i.message}" for i in report.warnings],
    )
    blob = json.dumps(payload, ensure_ascii=False)
    assert "question" in payload
    assert "plan" in payload
    assert "result" in payload
    assert "metadata" in payload
    assert "source_df" not in payload
    assert "raw_dataframe" not in blob.lower()
    # result is records only, not full original length dump beyond limit
    assert isinstance(payload["result"], list)


def test_interpreter_prompt_contains_no_recompute_constraint(monkeypatch) -> None:
    plan, result, meta, _report = _run(
        {
            "operation": "group_comparison",
            "group_column": "부서",
            "groups": ["영업", "연구"],
            "numerator": "비용",
            "denominator": "매출",
            "rate_name": "비율",
            "prefer_subtotals": True,
            "interpret": True,
        }
    )
    captured: dict[str, str] = {}

    def fake_chat(user: str, *, system: str = "", **_k):
        captured["user"] = user
        captured["system"] = system
        return "영업이 더 높습니다."

    text = interpret_analysis_result(
        "영업과 연구를 비교해줘",
        result,
        plan,
        exec_meta=meta,
        validation_warnings=["limit_underfilled: demo"],
        base_url="http://localhost",
        model="dummy",
        chat_text_fn=fake_chat,
        profile_name="generic",
    )
    assert text
    assert "Do not recompute" in captured["system"] or "do not recompute" in captured["system"].lower()
    assert "source of truth" in captured["system"].lower()
    assert "Validated analysis payload" in captured["user"]
    assert "limit_underfilled" in captured["user"]
    for token in _INTERPRETER_HARD_CONSTRAINTS.split(". ")[:3]:
        assert token.strip() in captured["system"] or token.lower() in captured["system"].lower()


def test_interpreter_does_not_invent_numbers_in_contract_payload() -> None:
    """Validated result 숫자만 payload에 들어가며, Interpreter가 다른 숫자를 만들 근거가 없다."""
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "compare_groups",
                    "group_column": "그룹",
                    "groups": ["A", "B"],
                    "metrics": ["집행률"],
                    "rate_columns": ["집행률"],
                }
            ],
            "criteria_note": "A vs B",
        },
        available_columns=["그룹", "집행률"],
    )
    result = pd.DataFrame(
        {"그룹": ["A", "B"], "집행률": [0.4060, 0.2808]}
    )
    meta = {
        "comparison": [
            {
                "metric": "집행률",
                "higher_group": "A",
                "diff_pp": 12.52,
            }
        ]
    }
    payload = build_interpreter_payload(
        "어디가 더 효율적인가",
        result,
        plan,
        exec_meta=meta,
    )
    blob = json.dumps(payload, ensure_ascii=False)
    assert "0.406" in blob or "0.4060" in blob
    assert "12.52" in blob
    # 원본 행/미검증 추가 수치 없음
    assert "실행예산" not in blob
    assert payload["metadata"]["comparison"][0]["diff_pp"] == 12.52
