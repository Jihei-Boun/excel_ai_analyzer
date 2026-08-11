"""Phase 17: Deterministic Integration Executor tests."""

from __future__ import annotations

import copy
from unittest.mock import patch

import pandas as pd
import pytest

from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.integration_validation_types import IntegrationValidationResult


def _valid() -> IntegrationValidationResult:
    return IntegrationValidationResult(valid=True, metadata={"phase": 16})


def _invalid() -> IntegrationValidationResult:
    return IntegrationValidationResult(valid=False, metadata={"phase": 16})


def _customers_orders():
    customers = pd.DataFrame(
        {
            "customer_id": [1, 2, 3],
            "name": ["A", "B", "C"],
            "status": ["active", "active", "inactive"],
        }
    )
    orders = pd.DataFrame(
        {
            "order_id": [10, 11, 12, 13],
            "customer_id": [1, 1, 2, 4],
            "amount": [100.0, 50.0, 20.0, 5.0],
            "status": ["paid", "open", "paid", "open"],
        }
    )
    return customers, orders


# ---------------------------------------------------------------------------
# Execution gate
# ---------------------------------------------------------------------------


def test_gate_valid_plan_runs() -> None:
    customers, orders = _customers_orders()
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["customers", "orders"],
                    "output": "joined",
                    "params": {
                        "left_keys": ["customer_id"],
                        "right_keys": ["customer_id"],
                        "how": "inner",
                    },
                }
            ],
            "final_output": "joined",
        }
    )
    result = execute_integration_plan(
        {"customers": customers, "orders": orders}, plan, _valid()
    )
    assert result.success
    assert result.final_output is not None
    assert len(result.final_output) == 3


def test_gate_invalid_validation_rejects() -> None:
    customers, orders = _customers_orders()
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "select_columns",
                    "inputs": ["customers"],
                    "output": "c",
                    "params": {"columns": ["customer_id"]},
                }
            ],
            "final_output": "c",
        }
    )
    result = execute_integration_plan(
        {"customers": customers, "orders": orders}, plan, _invalid()
    )
    assert not result.success
    assert result.error is not None
    assert result.error.code == "execution_gate_rejected"
    assert result.final_output is None


def test_gate_cannot_plan_rejects() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "cannot_plan",
            "steps": [],
            "final_output": None,
            "reason": "ambiguous keys",
        }
    )
    result = execute_integration_plan({"a": pd.DataFrame({"x": [1]})}, plan, _valid())
    assert not result.success
    assert result.error is not None
    assert result.error.code == "execution_gate_rejected"


def test_gate_missing_validation_rejects() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "select_columns",
                    "inputs": ["customers"],
                    "output": "c",
                    "params": {"columns": ["customer_id"]},
                }
            ],
            "final_output": "c",
        }
    )
    customers, _ = _customers_orders()
    result = execute_integration_plan({"customers": customers}, plan, None)
    assert not result.success
    assert result.error is not None
    assert result.error.code == "validation_required"


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


def test_rename_and_source_immutability() -> None:
    df = pd.DataFrame({"old": [1, 2], "keep": ["a", "b"]})
    before = df.copy(deep=True)
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "rename_columns",
                    "inputs": ["t"],
                    "output": "renamed",
                    "params": {"mapping": {"old": "new"}},
                }
            ],
            "final_output": "renamed",
        }
    )
    plan_before = copy.deepcopy(plan.to_dict())
    result = execute_integration_plan({"t": df}, plan, _valid())
    assert result.success
    assert list(result.final_output.columns) == ["new", "keep"]
    assert df.equals(before)
    assert plan.to_dict() == plan_before


# ---------------------------------------------------------------------------
# filter (Phase 15 contract ops only)
# ---------------------------------------------------------------------------


def test_filter_ops_eq_ne_comparisons() -> None:
    df = pd.DataFrame({"x": [1, 2, 3, 4], "y": [1, 0, 3, 9], "g": ["a", "b", "a", "c"]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "filter_rows",
                    "inputs": ["t"],
                    "output": "f",
                    "params": {
                        "conditions": [
                            {"column": "g", "operator": "eq", "value": "a"},
                        ]
                    },
                }
            ],
            "final_output": "f",
        }
    )
    result = execute_integration_plan({"t": df}, plan, _valid())
    assert result.success
    assert list(result.final_output["x"]) == [1, 3]


@pytest.mark.parametrize(
    "op,value,expected",
    [
        ("gt", 2, [3, 4]),
        ("gte", 3, [3, 4]),
        ("lt", 3, [1, 2]),
        ("lte", 2, [1, 2]),
        ("ne", 2, [1, 3, 4]),
    ],
)
def test_filter_inequalities(op: str, value: int, expected: list[int]) -> None:
    df = pd.DataFrame({"x": [1, 2, 3, 4]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "filter_rows",
                    "inputs": ["t"],
                    "output": "f",
                    "params": {
                        "conditions": [{"column": "x", "operator": op, "value": value}]
                    },
                }
            ],
            "final_output": "f",
        }
    )
    result = execute_integration_plan({"t": df}, plan, _valid())
    assert result.success
    assert list(result.final_output["x"]) == expected


def test_filter_column_vs_column_explicit_right_column() -> None:
    df = pd.DataFrame({"stock": [5, 1, 8], "safety_stock": [3, 2, 10]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "filter_rows",
                    "inputs": ["inv"],
                    "output": "low",
                    "params": {
                        "conditions": [
                            {
                                "left_column": "stock",
                                "operator": "lt",
                                "right_column": "safety_stock",
                            }
                        ]
                    },
                }
            ],
            "final_output": "low",
        }
    )
    result = execute_integration_plan({"inv": df}, plan, _valid())
    assert result.success
    assert list(result.final_output["stock"]) == [1, 8]
    assert plan.steps[0].params["conditions"][0].get("right_column") == "safety_stock"


def test_filter_value_matching_column_name_is_literal_not_promoted() -> None:
    """value that equals a column name must remain a literal (no column promotion)."""
    df = pd.DataFrame(
        {
            "label": ["safety_stock", "other", "safety_stock"],
            "safety_stock": [1, 2, 3],
        }
    )
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "filter_rows",
                    "inputs": ["t"],
                    "output": "f",
                    "params": {
                        "conditions": [
                            {
                                "column": "label",
                                "operator": "eq",
                                "value": "safety_stock",
                            }
                        ]
                    },
                }
            ],
            "final_output": "f",
        }
    )
    result = execute_integration_plan({"t": df}, plan, _valid())
    assert result.success
    assert len(result.final_output) == 2
    assert list(result.final_output["label"]) == ["safety_stock", "safety_stock"]


def test_filter_null_excluded_from_true() -> None:
    df = pd.DataFrame({"x": [1.0, None, 3.0]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "filter_rows",
                    "inputs": ["t"],
                    "output": "f",
                    "params": {
                        "conditions": [{"column": "x", "operator": "gt", "value": 0}]
                    },
                }
            ],
            "final_output": "f",
        }
    )
    result = execute_integration_plan({"t": df}, plan, _valid())
    assert result.success
    assert list(result.final_output["x"]) == [1.0, 3.0]
    assert result.step_results[0].metadata["null_policy"] == "comparison_true_only"


# ---------------------------------------------------------------------------
# union
# ---------------------------------------------------------------------------


def test_union_same_schema_deterministic_order() -> None:
    a = pd.DataFrame({"product": ["p1", "p2"], "qty": [1, 2]})
    b = pd.DataFrame({"product": ["p3"], "qty": [3]})
    a_before, b_before = a.copy(), b.copy()
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "union_rows",
                    "inputs": ["jan", "feb"],
                    "output": "combined",
                    "params": {"column_policy": "aligned"},
                }
            ],
            "final_output": "combined",
        }
    )
    r1 = execute_integration_plan({"jan": a, "feb": b}, plan, _valid())
    r2 = execute_integration_plan({"jan": a, "feb": b}, plan, _valid())
    assert r1.success and r2.success
    assert list(r1.final_output["product"]) == ["p1", "p2", "p3"]
    assert list(r1.final_output.columns) == ["product", "qty"]
    assert r1.final_output.equals(r2.final_output)
    assert a.equals(a_before) and b.equals(b_before)


def test_union_compatible_extra_columns_stable_order() -> None:
    a = pd.DataFrame({"id": [1], "x": [10]})
    b = pd.DataFrame({"id": [2], "y": [20]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "union_rows",
                    "inputs": ["a", "b"],
                    "output": "u",
                    "params": {"column_policy": "union_with_nulls"},
                }
            ],
            "final_output": "u",
        }
    )
    result = execute_integration_plan({"a": a, "b": b}, plan, _valid())
    assert result.success
    assert list(result.final_output.columns) == ["id", "x", "y"]


# ---------------------------------------------------------------------------
# join
# ---------------------------------------------------------------------------


def test_join_one_to_one_inner() -> None:
    left = pd.DataFrame({"id": [1, 2], "v": ["a", "b"]})
    right = pd.DataFrame({"id": [1, 2], "w": [10, 20]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["left", "right"],
                    "output": "j",
                    "params": {"left_keys": ["id"], "right_keys": ["id"], "how": "inner"},
                }
            ],
            "final_output": "j",
        }
    )
    result = execute_integration_plan({"left": left, "right": right}, plan, _valid())
    assert result.success
    assert len(result.final_output) == 2
    meta = result.step_results[0].metadata
    assert meta["actual_amplification_ratio"] == pytest.approx(1.0)
    assert meta["left_unmatched_count"] == 0


def test_join_one_to_many_left_preserves_direction() -> None:
    customers, orders = _customers_orders()
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["customers", "orders"],
                    "output": "j",
                    "params": {
                        "left_keys": ["customer_id"],
                        "right_keys": ["customer_id"],
                        "how": "left",
                    },
                }
            ],
            "final_output": "j",
        }
    )
    result = execute_integration_plan(
        {"customers": customers, "orders": orders}, plan, _valid()
    )
    assert result.success
    meta = result.step_results[0].metadata
    assert meta["left_input"] == "customers"
    assert meta["right_input"] == "orders"
    assert meta["left_rows"] == 3
    assert meta["right_rows"] == 4
    assert meta["output_rows"] == 4
    assert "name" in result.final_output.columns


def test_join_suffix_overlapping_non_key() -> None:
    customers, orders = _customers_orders()
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["customers", "orders"],
                    "output": "j",
                    "params": {
                        "left_keys": ["customer_id"],
                        "right_keys": ["customer_id"],
                        "how": "inner",
                    },
                }
            ],
            "final_output": "j",
        }
    )
    result = execute_integration_plan(
        {"customers": customers, "orders": orders}, plan, _valid()
    )
    assert result.success
    cols = list(result.final_output.columns)
    assert "status_left" in cols and "status_right" in cols
    assert result.step_results[0].metadata["suffixes"] == ["_left", "_right"]


def test_join_composite_key() -> None:
    left = pd.DataFrame({"a": [1, 1], "b": [10, 20], "v": [1, 2]})
    right = pd.DataFrame({"a": [1, 1], "b": [10, 30], "w": [9, 8]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["L", "R"],
                    "output": "j",
                    "params": {
                        "left_keys": ["a", "b"],
                        "right_keys": ["a", "b"],
                        "how": "inner",
                    },
                }
            ],
            "final_output": "j",
        }
    )
    result = execute_integration_plan({"L": left, "R": right}, plan, _valid())
    assert result.success
    assert len(result.final_output) == 1
    assert int(result.final_output.iloc[0]["v"]) == 1


def test_join_amplification_metadata_measured_not_fixed() -> None:
    left = pd.DataFrame({"k": [1, 1], "x": [1, 2]})
    right = pd.DataFrame({"k": [1, 1], "y": [3, 4]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["L", "R"],
                    "output": "j",
                    "params": {
                        "left_keys": ["k"],
                        "right_keys": ["k"],
                        "how": "inner",
                    },
                }
            ],
            "final_output": "j",
        }
    )
    result = execute_integration_plan({"L": left, "R": right}, plan, _valid())
    assert result.success
    assert len(result.final_output) == 4
    meta = result.step_results[0].metadata
    assert meta["actual_amplification_ratio"] == pytest.approx(2.0)
    assert meta["output_rows"] == 4


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fn", ["sum", "mean", "count", "min", "max", "median"])
def test_aggregate_functions(fn: str) -> None:
    df = pd.DataFrame({"g": ["a", "a", "b"], "v": [1.0, 3.0, 10.0]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "aggregate",
                    "inputs": ["t"],
                    "output": "agg",
                    "params": {
                        "group_by": ["g"],
                        "metrics": [{"column": "v", "function": fn, "alias": "out"}],
                    },
                }
            ],
            "final_output": "agg",
        }
    )
    result = execute_integration_plan({"t": df}, plan, _valid())
    assert result.success
    assert "out" in result.final_output.columns
    assert list(result.final_output["g"]) == ["a", "b"]


def test_aggregate_multiple_metrics() -> None:
    df = pd.DataFrame({"g": ["a", "a"], "v": [1.0, 3.0]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "aggregate",
                    "inputs": ["t"],
                    "output": "agg",
                    "params": {
                        "group_by": ["g"],
                        "metrics": [
                            {"column": "v", "function": "sum", "alias": "total"},
                            {"column": "v", "function": "mean", "alias": "avg"},
                        ],
                    },
                }
            ],
            "final_output": "agg",
        }
    )
    result = execute_integration_plan({"t": df}, plan, _valid())
    assert result.success
    assert float(result.final_output.iloc[0]["total"]) == 4.0
    assert float(result.final_output.iloc[0]["avg"]) == 2.0


# ---------------------------------------------------------------------------
# select
# ---------------------------------------------------------------------------


def test_select_preserves_plan_column_order() -> None:
    df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "select_columns",
                    "inputs": ["t"],
                    "output": "s",
                    "params": {"columns": ["c", "a"]},
                }
            ],
            "final_output": "s",
        }
    )
    result = execute_integration_plan({"t": df}, plan, _valid())
    assert result.success
    assert list(result.final_output.columns) == ["c", "a"]


# ---------------------------------------------------------------------------
# composition
# ---------------------------------------------------------------------------


def test_composition_union_then_aggregate() -> None:
    a = pd.DataFrame({"product": ["p1", "p1"], "qty": [1, 2]})
    b = pd.DataFrame({"product": ["p1", "p2"], "qty": [3, 4]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "id": "step_1",
                    "op": "union_rows",
                    "inputs": ["jan", "feb"],
                    "output": "combined",
                    "params": {"column_policy": "aligned"},
                },
                {
                    "id": "step_2",
                    "op": "aggregate",
                    "inputs": ["combined"],
                    "output": "summary",
                    "params": {
                        "group_by": ["product"],
                        "metrics": [{"column": "qty", "function": "sum", "alias": "total"}],
                    },
                },
            ],
            "final_output": "summary",
        }
    )
    result = execute_integration_plan({"jan": a, "feb": b}, plan, _valid())
    assert result.success
    by = dict(zip(result.final_output["product"], result.final_output["total"]))
    assert by["p1"] == 6
    assert by["p2"] == 4


def test_composition_join_then_select() -> None:
    customers, orders = _customers_orders()
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["customers", "orders"],
                    "output": "joined",
                    "params": {
                        "left_keys": ["customer_id"],
                        "right_keys": ["customer_id"],
                        "how": "inner",
                    },
                },
                {
                    "op": "select_columns",
                    "inputs": ["joined"],
                    "output": "final",
                    "params": {"columns": ["name", "amount"]},
                },
            ],
            "final_output": "final",
        }
    )
    result = execute_integration_plan(
        {"customers": customers, "orders": orders}, plan, _valid()
    )
    assert result.success
    assert list(result.final_output.columns) == ["name", "amount"]


def test_composition_filter_union_aggregate() -> None:
    a = pd.DataFrame({"sku": ["x", "y"], "qty": [1, 2]})
    b = pd.DataFrame({"sku": ["x", "z"], "qty": [5, 9]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "filter_rows",
                    "inputs": ["a"],
                    "output": "a2",
                    "params": {
                        "conditions": [{"column": "qty", "operator": "gte", "value": 2}]
                    },
                },
                {
                    "op": "union_rows",
                    "inputs": ["a2", "b"],
                    "output": "u",
                    "params": {"column_policy": "aligned"},
                },
                {
                    "op": "aggregate",
                    "inputs": ["u"],
                    "output": "s",
                    "params": {
                        "group_by": ["sku"],
                        "metrics": [{"column": "qty", "function": "sum", "alias": "total"}],
                    },
                },
            ],
            "final_output": "s",
        }
    )
    result = execute_integration_plan({"a": a, "b": b}, plan, _valid())
    assert result.success
    by = dict(zip(result.final_output["sku"], result.final_output["total"]))
    assert by["y"] == 2
    assert by["x"] == 5


def test_composition_rename_join_aggregate_select() -> None:
    customers = pd.DataFrame({"cid": [1, 2], "name": ["A", "B"]})
    orders = pd.DataFrame({"customer_id": [1, 1, 2], "amount": [10.0, 5.0, 7.0]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "rename_columns",
                    "inputs": ["customers"],
                    "output": "c2",
                    "params": {"mapping": {"cid": "customer_id"}},
                },
                {
                    "op": "join",
                    "inputs": ["c2", "orders"],
                    "output": "j",
                    "params": {
                        "left_keys": ["customer_id"],
                        "right_keys": ["customer_id"],
                        "how": "inner",
                    },
                },
                {
                    "op": "aggregate",
                    "inputs": ["j"],
                    "output": "agg",
                    "params": {
                        "group_by": ["name"],
                        "metrics": [
                            {"column": "amount", "function": "sum", "alias": "total_amount"}
                        ],
                    },
                },
                {
                    "op": "select_columns",
                    "inputs": ["agg"],
                    "output": "final",
                    "params": {"columns": ["name", "total_amount"]},
                },
            ],
            "final_output": "final",
        }
    )
    result = execute_integration_plan(
        {"customers": customers, "orders": orders}, plan, _valid()
    )
    assert result.success
    by = dict(zip(result.final_output["name"], result.final_output["total_amount"]))
    assert by["A"] == 15.0
    assert by["B"] == 7.0
    assert len(result.lineage) == 4


# ---------------------------------------------------------------------------
# Failure / no recovery
# ---------------------------------------------------------------------------


def test_runtime_missing_column_stops_pipeline() -> None:
    df = pd.DataFrame({"a": [1]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "id": "step_1",
                    "op": "select_columns",
                    "inputs": ["t"],
                    "output": "s",
                    "params": {"columns": ["missing"]},
                },
                {
                    "id": "step_2",
                    "op": "rename_columns",
                    "inputs": ["s"],
                    "output": "r",
                    "params": {"mapping": {"missing": "x"}},
                },
            ],
            "final_output": "r",
        }
    )
    result = execute_integration_plan({"t": df}, plan, _valid())
    assert not result.success
    assert result.error is not None
    assert result.error.code == "missing_column"
    assert result.error.step_id == "step_1"
    assert not any(s.step_id == "step_2" and s.status == "success" for s in result.step_results)


def test_missing_dataset_fails() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "select_columns",
                    "inputs": ["ghost"],
                    "output": "s",
                    "params": {"columns": ["a"]},
                }
            ],
            "final_output": "s",
        }
    )
    result = execute_integration_plan({"t": pd.DataFrame({"a": [1]})}, plan, _valid())
    assert not result.success
    assert result.error.code == "missing_dataset"


def test_no_semantic_autocomplete_merge_engine_not_called() -> None:
    customers, orders = _customers_orders()
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["customers", "orders"],
                    "output": "j",
                    "params": {
                        "left_keys": ["customer_id"],
                        "right_keys": ["customer_id"],
                        "how": "inner",
                    },
                }
            ],
            "final_output": "j",
        }
    )
    with patch("core.io.merge_engine.merge_named_frames") as mocked_merge:
        with patch("core.io.merge_engine.infer_common_keys") as mocked_infer:
            result = execute_integration_plan(
                {"customers": customers, "orders": orders}, plan, _valid()
            )
            assert result.success
            mocked_merge.assert_not_called()
            mocked_infer.assert_not_called()


def test_executor_does_not_flip_join_direction() -> None:
    left = pd.DataFrame({"k": [1, 2, 3, 4], "L": [1, 1, 1, 1]})
    right = pd.DataFrame({"k": [1], "R": [9]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["left", "right"],
                    "output": "j",
                    "params": {
                        "left_keys": ["k"],
                        "right_keys": ["k"],
                        "how": "left",
                    },
                }
            ],
            "final_output": "j",
        }
    )
    result = execute_integration_plan({"left": left, "right": right}, plan, _valid())
    assert result.success
    assert result.step_results[0].metadata["left_rows"] == 4
    assert result.step_results[0].metadata["right_rows"] == 1
    assert len(result.final_output) == 4


def test_executor_does_not_drop_duplicates_or_subtotals() -> None:
    df = pd.DataFrame(
        {
            "product": ["A", "A", "TOTAL"],
            "amount": [1.0, 1.0, 2.0],
        }
    )
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "aggregate",
                    "inputs": ["t"],
                    "output": "agg",
                    "params": {
                        "group_by": ["product"],
                        "metrics": [
                            {"column": "amount", "function": "sum", "alias": "total"}
                        ],
                    },
                }
            ],
            "final_output": "agg",
        }
    )
    result = execute_integration_plan({"t": df}, plan, _valid())
    assert result.success
    assert "TOTAL" in set(result.final_output["product"])
    by = dict(zip(result.final_output["product"], result.final_output["total"]))
    assert by["A"] == 2.0
    assert by["TOTAL"] == 2.0


def test_plan_immutability() -> None:
    df = pd.DataFrame({"a": [1, 2]})
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "filter_rows",
                    "inputs": ["t"],
                    "output": "f",
                    "params": {
                        "conditions": [{"column": "a", "operator": "gt", "value": 1}]
                    },
                }
            ],
            "final_output": "f",
        }
    )
    before = copy.deepcopy(plan.to_dict())
    result = execute_integration_plan({"t": df}, plan, _valid())
    assert result.success
    assert plan.to_dict() == before
