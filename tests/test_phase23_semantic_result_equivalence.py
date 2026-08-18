"""Phase 23: Semantic result equivalence (LLM-free)."""

from __future__ import annotations

import pandas as pd

from tests.benchmark_multi.schema import load_case_dict
from tests.benchmark_multi.semantic_compare import (
    compare_semantic_result,
    extract_aggregate_metrics,
    map_expected_metric_to_actual_column,
)


def _agg_case(**kwargs):
    base = {
        "id": "t",
        "files": ["a.xlsx", "b.xlsx"],
        "prompt": "sum by region",
        "scenario": "join_aggregate",
        "fixed_plan": {
            "status": "planned",
            "final_output": "summary",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["a", "b"],
                    "output": "j",
                    "params": {
                        "left_keys": ["id"],
                        "right_keys": ["id"],
                        "how": "inner",
                    },
                },
                {
                    "op": "aggregate",
                    "inputs": ["j"],
                    "output": "summary",
                    "params": {
                        "group_by": ["region"],
                        "metrics": [
                            {
                                "column": "amount",
                                "function": "sum",
                                "alias": "total_amount",
                            }
                        ],
                    },
                },
            ],
        },
        "expected": {
            "pipeline_status": "success",
            "required_operations": ["join", "aggregate"],
            "result": {
                "required_columns": ["region", "total_amount"],
                "result_compare": {
                    "key_column": "region",
                    "value_column": "total_amount",
                    "expected_result": {"East": 10.0, "West": 5.0},
                },
            },
        },
    }
    base["expected"]["result"].update(kwargs.pop("result_extra", {}) or {})
    base.update(kwargs)
    return load_case_dict(base)


def test_same_semantic_aggregate_different_alias_equivalent() -> None:
    case = _agg_case()
    plan = {
        "status": "planned",
        "final_output": "summary",
        "steps": [
            {
                "op": "join",
                "inputs": ["a", "b"],
                "output": "j",
                "params": {
                    "left_keys": ["id"],
                    "right_keys": ["id"],
                    "how": "inner",
                },
            },
            {
                "op": "aggregate",
                "inputs": ["j"],
                "output": "summary",
                "params": {
                    "group_by": ["region"],
                    "metrics": [
                        {
                            "column": "amount",
                            "function": "sum",
                            "alias": "total_order_amount",
                        }
                    ],
                },
            },
        ],
    }
    df = pd.DataFrame({"region": ["East", "West"], "total_order_amount": [10.0, 5.0]})
    r = compare_semantic_result(case, plan_dict=plan, final_df=df)
    assert r.ok
    assert r.semantic_equivalent
    assert r.alias_only_mismatch or r.representation_only
    assert r.column_mapping.get("total_amount") == "total_order_amount"


def test_same_alias_different_source_metric_fails() -> None:
    case = _agg_case()
    plan = {
        "status": "planned",
        "final_output": "summary",
        "steps": [
            {
                "op": "aggregate",
                "inputs": ["j"],
                "output": "summary",
                "params": {
                    "group_by": ["region"],
                    "metrics": [
                        {
                            "column": "quantity",
                            "function": "sum",
                            "alias": "total_amount",
                        }
                    ],
                },
            }
        ],
    }
    df = pd.DataFrame({"region": ["East", "West"], "total_amount": [10.0, 5.0]})
    r = compare_semantic_result(case, plan_dict=plan, final_df=df)
    assert not r.ok
    assert r.true_semantic_mismatch
    assert "metric_identity_mismatch" in r.reasons


def test_same_alias_different_aggregation_fn_fails() -> None:
    case = _agg_case()
    plan = {
        "status": "planned",
        "final_output": "summary",
        "steps": [
            {
                "op": "aggregate",
                "inputs": ["j"],
                "output": "summary",
                "params": {
                    "group_by": ["region"],
                    "metrics": [
                        {
                            "column": "amount",
                            "function": "mean",
                            "alias": "total_amount",
                        }
                    ],
                },
            }
        ],
    }
    df = pd.DataFrame({"region": ["East", "West"], "total_amount": [10.0, 5.0]})
    r = compare_semantic_result(case, plan_dict=plan, final_df=df)
    assert not r.ok
    assert r.true_semantic_mismatch


def test_coincidental_values_wrong_operation_fails() -> None:
    """Values match expected but metric identity wrong → fail."""
    case = _agg_case()
    plan = {
        "status": "planned",
        "final_output": "summary",
        "steps": [
            {
                "op": "aggregate",
                "inputs": ["j"],
                "output": "summary",
                "params": {
                    "group_by": ["region"],
                    "metrics": [
                        {
                            "column": "amount",
                            "function": "max",
                            "alias": "total_amount",
                        }
                    ],
                },
            }
        ],
    }
    df = pd.DataFrame({"region": ["East", "West"], "total_amount": [10.0, 5.0]})
    r = compare_semantic_result(case, plan_dict=plan, final_df=df)
    assert not r.ok


def test_correct_values_wrong_grain_fails() -> None:
    case = load_case_dict(
        {
            "id": "detail",
            "files": ["a.xlsx", "b.xlsx"],
            "prompt": "connect rows",
            "scenario": "composite_key_join",
            "fixed_plan": {
                "status": "planned",
                "final_output": "j",
                "steps": [
                    {
                        "op": "join",
                        "inputs": ["a", "b"],
                        "output": "j",
                        "params": {
                            "left_keys": ["k"],
                            "right_keys": ["k"],
                            "how": "inner",
                        },
                    }
                ],
            },
            "expected": {
                "required_operations": ["join"],
                "result": {
                    "expected_row_count": 4,
                    "required_columns": ["k", "units"],
                    "expected_grain": "detail",
                },
            },
        }
    )
    plan = {
        "status": "planned",
        "final_output": "agg",
        "steps": [
            {
                "op": "join",
                "inputs": ["a", "b"],
                "output": "j",
                "params": {
                    "left_keys": ["k"],
                    "right_keys": ["k"],
                    "how": "inner",
                },
            },
            {
                "op": "aggregate",
                "inputs": ["j"],
                "output": "agg",
                "params": {
                    "group_by": ["k"],
                    "metrics": [
                        {"column": "units", "function": "sum", "alias": "units"}
                    ],
                },
            },
        ],
    }
    df = pd.DataFrame({"k": [1, 2, 3, 4], "units": [1, 1, 1, 1]})
    r = compare_semantic_result(case, plan_dict=plan, final_df=df)
    assert not r.ok
    assert r.grain_mismatch


def test_numeric_tolerance_unchanged() -> None:
    case = _agg_case()
    plan = dict(case.fixed_plan)
    df = pd.DataFrame({"region": ["East", "West"], "total_amount": [10.0 + 1e-9, 5.0]})
    r = compare_semantic_result(case, plan_dict=plan, final_df=df)
    assert r.ok
    df_bad = pd.DataFrame({"region": ["East", "West"], "total_amount": [10.01, 5.0]})
    r2 = compare_semantic_result(case, plan_dict=plan, final_df=df_bad)
    assert not r2.ok
    assert r2.true_semantic_mismatch


def test_map_expected_metric_lineage_not_string_similarity() -> None:
    actual = [
        {
            "source_column": "amount",
            "aggregation": "sum",
            "alias": "zzz_unrelated_name",
        }
    ]
    df = pd.DataFrame({"zzz_unrelated_name": [1.0]})
    mapped = map_expected_metric_to_actual_column(
        {"source_column": "amount", "aggregation": "sum", "alias": "total_amount"},
        actual,
        df,
    )
    assert mapped == "zzz_unrelated_name"
    # Similar alias but wrong source must not map via similarity
    actual2 = [
        {
            "source_column": "qty",
            "aggregation": "sum",
            "alias": "total_amount",
        }
    ]
    mapped2 = map_expected_metric_to_actual_column(
        {"source_column": "amount", "aggregation": "sum", "alias": "total_amount"},
        actual2,
        pd.DataFrame({"total_amount": [1.0]}),
    )
    assert mapped2 is None


def test_extract_metrics_from_plan() -> None:
    plan = {
        "steps": [
            {
                "op": "aggregate",
                "params": {
                    "metrics": [
                        {"column": "a", "function": "sum", "alias": "sa"},
                        {"column": "b", "fn": "mean"},
                    ]
                },
            }
        ]
    }
    ms = extract_aggregate_metrics(plan)
    assert ("a", "sum") in {(m["source_column"], m["aggregation"]) for m in ms}
    assert any(m["alias"] == "b" for m in ms)  # default alias = column


def test_evaluator_no_case_id_hardcoding() -> None:
    import inspect
    import tests.benchmark_multi.evaluate as ev
    import tests.benchmark_multi.semantic_compare as sc

    for mod in (ev, sc):
        src = inspect.getsource(mod)
        assert "case_id ==" not in src
        assert 'case.id ==' not in src
        assert "budget_001" not in src
        assert "join_aggregate_001" not in src
