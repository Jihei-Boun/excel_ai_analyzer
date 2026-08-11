"""Unit tests for multi-file benchmark evaluator/metrics (harness correctness)."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from tests.benchmark_multi.evaluate import evaluate_case, _is_unsafe_execution
from tests.benchmark_multi.metrics import summarize_multi_run, summarize_results
from tests.benchmark_multi.schema import load_case_dict


def _case(**kwargs):
    base = {
        "id": "t1",
        "files": ["a.xlsx", "b.xlsx"],
        "prompt": "x",
        "scenario": "master_detail_join",
        "domain": "orders",
        "expected": {
            "pipeline_status": "success",
            "safety_outcome": "safe",
            "required_operations": ["join"],
            "join": {"left_keys": ["customer_id"], "right_keys": ["customer_id"]},
            "result": {
                "expected_row_count": 2,
                "required_columns": ["customer_id", "amount"],
                "result_compare": {
                    "key_column": "customer_id",
                    "value_column": "amount",
                    "expected_result": {"1": 10.0, "2": 20.0},
                },
            },
        },
    }
    base.update(kwargs)
    return load_case_dict(base)


def test_evaluator_success_count_and_golden() -> None:
    case = _case()
    df = pd.DataFrame({"customer_id": [1, 2], "amount": [10.0, 20.0]})
    plan = SimpleNamespace(
        to_dict=lambda: {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["a", "b"],
                    "output": "j",
                    "params": {
                        "left_keys": ["customer_id"],
                        "right_keys": ["customer_id"],
                        "how": "inner",
                    },
                }
            ],
        }
    )
    pipeline = SimpleNamespace(
        status="success",
        plan=plan,
        plan_validation=SimpleNamespace(valid=True, errors=[]),
        execution=SimpleNamespace(success=True, step_results=[]),
        result_validation=SimpleNamespace(valid=True, errors=[], warnings=[]),
        retry_log=[],
        final_output=df,
        metadata={"first_plan_success": True, "retry_count": 0, "attempt_count": 1},
    )
    ev = evaluate_case(case, pipeline=pipeline, understanding=None)
    assert ev["status_ok"]
    assert ev["overall_ok"]
    assert ev["safe_outcome"]
    assert not ev["unsafe_execution"]
    assert ev["levels"]["L4_execution"]["ok"]


def test_evaluator_cannot_plan_is_safe_success() -> None:
    case = _case(
        scenario="ambiguous_key",
        expected={
            "pipeline_status": ["cannot_plan"],
            "safety_outcome": "safe",
            "forbidden_operations": ["join"],
        },
    )
    pipeline = SimpleNamespace(
        status="cannot_plan",
        plan=SimpleNamespace(to_dict=lambda: {"status": "cannot_plan", "steps": []}),
        plan_validation=None,
        execution=None,
        result_validation=None,
        retry_log=[],
        final_output=None,
        metadata={},
    )
    ev = evaluate_case(case, pipeline=pipeline, understanding=None)
    assert ev["correct_cannot_plan"]
    assert ev["safe_outcome"]
    assert ev["overall_ok"]


def test_evaluator_unsafe_when_success_on_cannot_plan_case() -> None:
    case = _case(
        scenario="unrelated",
        expected={
            "pipeline_status": ["cannot_plan"],
            "safety_outcome": "safe",
            "forbidden_operations": ["join"],
        },
    )
    plan = SimpleNamespace(
        to_dict=lambda: {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["a", "b"],
                    "output": "j",
                    "params": {
                        "left_keys": ["x"],
                        "right_keys": ["x"],
                        "how": "inner",
                    },
                }
            ],
        }
    )
    pipeline = SimpleNamespace(
        status="success",
        plan=plan,
        plan_validation=SimpleNamespace(valid=True, errors=[]),
        execution=SimpleNamespace(success=True, step_results=[]),
        result_validation=SimpleNamespace(valid=True, errors=[], warnings=[]),
        retry_log=[],
        final_output=pd.DataFrame({"x": [1]}),
        metadata={},
    )
    ev = evaluate_case(case, pipeline=pipeline, understanding=None)
    assert ev["unsafe_execution"]
    assert not ev["safe_outcome"]


def test_wrong_join_key_flag() -> None:
    case = _case()
    plan = SimpleNamespace(
        to_dict=lambda: {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["a", "b"],
                    "output": "j",
                    "params": {
                        "left_keys": ["wrong"],
                        "right_keys": ["customer_id"],
                        "how": "inner",
                    },
                }
            ],
        }
    )
    pipeline = SimpleNamespace(
        status="success",
        plan=plan,
        plan_validation=SimpleNamespace(valid=True, errors=[]),
        execution=SimpleNamespace(success=True, step_results=[]),
        result_validation=SimpleNamespace(valid=True, errors=[], warnings=[]),
        retry_log=[],
        final_output=pd.DataFrame({"customer_id": [1, 2], "amount": [10.0, 20.0]}),
        metadata={},
    )
    ev = evaluate_case(case, pipeline=pipeline, understanding=None)
    assert ev["planner_quality"].get("wrong_join_key")


def test_retry_success_metric_aggregation() -> None:
    cases = [
        {
            "case_id": "a",
            "scenario": "x",
            "domain": "d",
            "status": "success",
            "status_ok": True,
            "overall_ok": True,
            "safe_outcome": True,
            "unsafe_execution": False,
            "correct_cannot_plan": False,
            "unnecessary_cannot_plan": False,
            "planner_quality": {},
            "failure_categories": [],
            "levels": {
                "L6_recovery": {
                    "first_plan_success": False,
                    "retry_count": 1,
                    "duplicate_plan_count": 0,
                    "plan_validation_failure_count": 1,
                    "execution_failure_count": 0,
                    "result_validation_failure_count": 0,
                }
            },
        },
        {
            "case_id": "b",
            "scenario": "x",
            "domain": "d",
            "status": "cannot_plan",
            "status_ok": True,
            "overall_ok": True,
            "safe_outcome": True,
            "unsafe_execution": False,
            "correct_cannot_plan": True,
            "unnecessary_cannot_plan": False,
            "planner_quality": {},
            "failure_categories": [],
            "levels": {"L6_recovery": {"first_plan_success": True, "retry_count": 0}},
        },
    ]
    summary = summarize_results(cases, mode="deterministic")
    assert summary["overall"]["pipeline_success_rate"] == 50.0
    assert summary["overall"]["safe_outcome_rate"] == 100.0
    assert summary["overall"]["unsafe_execution_rate"] == 0.0
    assert summary["overall"]["retry_success_rate"] == 50.0


def test_multi_run_stats() -> None:
    s1 = {"overall": {"pipeline_success_rate": 40.0, "safe_outcome_rate": 90.0, "unsafe_execution_rate": 0.0, "first_plan_success_rate": 30.0, "retry_success_rate": 10.0, "cannot_plan_rate": 50.0, "overall_ok_rate": 80.0}, "planner_quality": {"wrong_join_key": 10.0, "wrong_operation": 5.0, "wrong_composition": 0.0}}
    s2 = {"overall": {"pipeline_success_rate": 50.0, "safe_outcome_rate": 100.0, "unsafe_execution_rate": 0.0, "first_plan_success_rate": 40.0, "retry_success_rate": 20.0, "cannot_plan_rate": 40.0, "overall_ok_rate": 90.0}, "planner_quality": {"wrong_join_key": 0.0, "wrong_operation": 5.0, "wrong_composition": 0.0}}
    s3 = {"overall": {"pipeline_success_rate": 45.0, "safe_outcome_rate": 95.0, "unsafe_execution_rate": 0.0, "first_plan_success_rate": 35.0, "retry_success_rate": 15.0, "cannot_plan_rate": 45.0, "overall_ok_rate": 85.0}, "planner_quality": {"wrong_join_key": 5.0, "wrong_operation": 5.0, "wrong_composition": 0.0}}
    multi = summarize_multi_run([s1, s2, s3])
    assert multi["runs"] == 3
    assert multi["metrics"]["safe_outcome_rate"]["mean"] == 95.0
    assert multi["metrics"]["unsafe_execution_rate"]["max"] == 0.0


def test_is_unsafe_many_to_many_success() -> None:
    case = _case(scenario="many_to_many", expected={"pipeline_status": ["cannot_plan"], "safety_outcome": "safe"})
    assert _is_unsafe_execution(
        case,
        status="success",
        plan_dict={"steps": [{"op": "join", "params": {}}]},
        execution=None,
    )
