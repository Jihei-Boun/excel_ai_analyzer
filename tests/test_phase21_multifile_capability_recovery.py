"""Phase 21: Multi-file capability recovery (LLM-free)."""

from __future__ import annotations

import pandas as pd

from core.integrate.integration_contracts import (
    FAILURE_TYPE_AMBIGUITY,
    FAILURE_TYPE_STRUCTURAL,
    classify_integration_failure_codes,
    default_aggregate_alias,
    materialize_aggregate_metric,
    resolve_aggregate_alias,
    retry_mode_for_failure_type,
)
from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.integration_validation_types import format_integration_validation_feedback
from core.integrate.relationship_profile import build_pairwise_observation


def test_aggregate_alias_contract_consistency() -> None:
    assert default_aggregate_alias("amount", "sum") == "amount"
    assert resolve_aggregate_alias({"column": "amount", "function": "sum"}) == "amount"
    assert (
        resolve_aggregate_alias(
            {"column": "amount", "function": "sum", "alias": "total_amount"}
        )
        == "total_amount"
    )
    m = materialize_aggregate_metric({"column": "stock", "function": "sum"})
    assert m["alias"] == "stock"
    m2 = materialize_aggregate_metric(
        {"column": "stock", "function": "sum", "alias": "total_stock"}
    )
    assert m2["alias"] == "total_stock"


def test_parser_materializes_alias() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "aggregate",
                    "inputs": ["u"],
                    "output": "agg",
                    "params": {
                        "group_by": ["sku"],
                        "metrics": [{"column": "qty", "function": "sum"}],
                    },
                }
            ],
            "final_output": "agg",
        }
    )
    assert plan.steps[0].params["metrics"][0]["alias"] == "qty"


def test_alias_dependency_schema_propagation() -> None:
    und = {
        "file_profiles": [
            {
                "source_id": "u",
                "row_count": 2,
                "column_count": 2,
                "observations": {
                    "column_names": ["sku", "qty"],
                    "columns": [
                        {
                            "name": "sku",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                        {
                            "name": "qty",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                    ],
                },
                "semantic_hints": {},
            }
        ],
        "pairwise_observations": [],
        "relationships": [],
    }
    # select references alias that does not exist → structural error
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "aggregate",
                    "inputs": ["u"],
                    "output": "agg",
                    "params": {
                        "group_by": ["sku"],
                        "metrics": [
                            {"column": "qty", "function": "sum", "alias": "total_qty"}
                        ],
                    },
                },
                {
                    "op": "select_columns",
                    "inputs": ["agg"],
                    "output": "out",
                    "params": {"columns": ["sku", "qty"]},  # wrong — should be total_qty
                },
            ],
            "final_output": "out",
        }
    )
    result = validate_integration_plan(und, plan)
    assert not result.valid
    assert any(i.code == "nonexistent_column" for i in result.errors)
    # Must not rewrite qty→total_qty
    assert plan.steps[1].params["columns"] == ["sku", "qty"]


def test_alias_mismatch_rejected_without_rewrite() -> None:
    sources = {"u": pd.DataFrame({"sku": ["a", "b"], "qty": [1, 2]})}
    und = {
        "file_profiles": [
            {
                "source_id": "u",
                "row_count": 2,
                "column_count": 2,
                "observations": {
                    "column_names": ["sku", "qty"],
                    "columns": [
                        {
                            "name": "sku",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                        {
                            "name": "qty",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                    ],
                },
                "semantic_hints": {},
            }
        ],
        "pairwise_observations": [],
        "relationships": [],
    }
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "aggregate",
                    "inputs": ["u"],
                    "output": "agg",
                    "params": {
                        "group_by": ["sku"],
                        "metrics": [
                            {"column": "qty", "function": "sum", "alias": "total_qty"}
                        ],
                    },
                }
            ],
            "final_output": "agg",
        }
    )
    val = validate_integration_plan(und, plan)
    assert val.valid
    exe = execute_integration_plan(sources, plan, val)
    assert exe.success
    assert list(exe.final_output.columns) == ["sku", "total_qty"]


def test_structural_alias_retry_feedback() -> None:
    und = {
        "file_profiles": [
            {
                "source_id": "u",
                "row_count": 1,
                "column_count": 2,
                "observations": {
                    "column_names": ["sku", "qty"],
                    "columns": [
                        {
                            "name": "sku",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 1,
                            "sample_values": [],
                        },
                        {
                            "name": "qty",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 1,
                            "sample_values": [],
                        },
                    ],
                },
                "semantic_hints": {},
            }
        ],
        "pairwise_observations": [],
        "relationships": [],
    }
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "aggregate",
                    "inputs": ["u"],
                    "output": "agg",
                    "params": {
                        "group_by": ["sku"],
                        "metrics": [
                            {"column": "qty", "function": "sum", "alias": "total_qty"}
                        ],
                    },
                },
                {
                    "op": "select_columns",
                    "inputs": ["agg"],
                    "output": "out",
                    "params": {"columns": ["missing_alias"]},
                },
            ],
            "final_output": "out",
        }
    )
    result = validate_integration_plan(und, plan)
    fb = "\n".join(format_integration_validation_feedback(result, previous_plan=plan.to_dict()))
    assert "structural_contract_failure" in fb
    assert "retry_mode: repair" in fb
    assert "total_qty" not in fb or "Use total_qty" not in fb  # no answer prescription


def test_valid_composite_key_observation() -> None:
    left = pd.DataFrame(
        {
            "store_id": ["S1", "S1", "S2", "S2"],
            "product_id": ["P1", "P2", "P1", "P2"],
            "qty": [1, 2, 3, 4],
        }
    )
    right = pd.DataFrame(
        {
            "store_id": ["S1", "S1", "S2", "S2"],
            "product_id": ["P1", "P2", "P1", "P2"],
            "price": [10, 20, 30, 40],
        }
    )
    obs = build_pairwise_observation("inv", left, "price", right)
    assert obs.key_ambiguity_observation.get("near_tied") is False
    comps = obs.composite_key_observations
    assert comps
    assert any(
        set(c["left_columns"]) == {"store_id", "product_id"}
        and c.get("constituents_individually_unique") is False
        for c in comps
    )


def test_composite_plan_not_blocked_by_singleton_ambiguity() -> None:
    left = pd.DataFrame(
        {
            "store_id": ["S1", "S1", "S2", "S2"],
            "product_id": ["P1", "P2", "P1", "P2"],
            "units": [1, 2, 3, 4],
        }
    )
    right = pd.DataFrame(
        {
            "store_id": ["S1", "S1", "S2", "S2"],
            "product_id": ["P1", "P2", "P1", "P2"],
            "unit_price": [10.0, 12.0, 20.0, 15.0],
        }
    )
    obs = build_pairwise_observation("sales_store", left, "price_store", right)
    und = {
        "file_profiles": [
            {
                "source_id": "sales_store",
                "row_count": 4,
                "column_count": 3,
                "observations": {
                    "column_names": list(left.columns),
                    "columns": [
                        {
                            "name": c,
                            "dtype_family": "numeric" if c == "units" else "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": left[c].nunique() / len(left),
                            "distinct_count": int(left[c].nunique()),
                            "sample_values": [],
                        }
                        for c in left.columns
                    ],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "price_store",
                "row_count": 4,
                "column_count": 3,
                "observations": {
                    "column_names": list(right.columns),
                    "columns": [
                        {
                            "name": c,
                            "dtype_family": "numeric" if c == "unit_price" else "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": right[c].nunique() / len(right),
                            "distinct_count": int(right[c].nunique()),
                            "sample_values": [],
                        }
                        for c in right.columns
                    ],
                },
                "semantic_hints": {},
            },
        ],
        "pairwise_observations": [obs.to_dict()],
        "relationships": [
            {
                "left_source": "sales_store",
                "right_source": "price_store",
                "relationship": "join_candidate",
                "key_candidates": [],
                "confidence": 0.7,
                "evidence": [],
                "ambiguities": [],
            }
        ],
    }
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["sales_store", "price_store"],
                    "output": "j",
                    "params": {
                        "left_keys": ["store_id", "product_id"],
                        "right_keys": ["store_id", "product_id"],
                        "how": "inner",
                    },
                }
            ],
            "final_output": "j",
        }
    )
    result = validate_integration_plan(und, plan)
    assert not any(i.code == "ambiguous_key_selection" for i in result.errors)
    assert not any(i.code == "many_to_many_join_risk" for i in result.errors)
    assert result.valid


def test_unresolved_singleton_ambiguity_still_blocked() -> None:
    a = pd.DataFrame(
        {
            "customer_id": ["C1", "C2", "C3", "C4"],
            "account_id": ["A1", "A2", "A3", "A4"],
            "name": ["n1", "n2", "n3", "n4"],
        }
    )
    b = pd.DataFrame(
        {
            "customer_id": ["C1", "C2", "C3", "C4"],
            "account_id": ["A1", "A2", "A3", "A4"],
            "score": [1, 2, 3, 4],
        }
    )
    obs = build_pairwise_observation("ambiguous_a", a, "ambiguous_b", b)
    assert obs.key_ambiguity_observation.get("near_tied") is True
    und = {
        "file_profiles": [
            {
                "source_id": "ambiguous_a",
                "row_count": 4,
                "column_count": 3,
                "observations": {
                    "column_names": list(a.columns),
                    "columns": [
                        {
                            "name": c,
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 4,
                            "sample_values": [],
                        }
                        for c in a.columns
                    ],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "ambiguous_b",
                "row_count": 4,
                "column_count": 3,
                "observations": {
                    "column_names": list(b.columns),
                    "columns": [
                        {
                            "name": c,
                            "dtype_family": "numeric" if c == "score" else "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 4,
                            "sample_values": [],
                        }
                        for c in b.columns
                    ],
                },
                "semantic_hints": {},
            },
        ],
        "pairwise_observations": [obs.to_dict()],
        "relationships": [
            {
                "left_source": "ambiguous_a",
                "right_source": "ambiguous_b",
                "relationship": "join_candidate",
                "key_candidates": [
                    {"left_column": "customer_id", "right_column": "customer_id"},
                    {"left_column": "account_id", "right_column": "account_id"},
                ],
                "confidence": 0.8,
                "evidence": [],
                "ambiguities": [],
            }
        ],
    }
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["ambiguous_a", "ambiguous_b"],
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
    result = validate_integration_plan(und, plan)
    assert any(i.code == "ambiguous_key_selection" for i in result.errors)


def test_weak_near_tie_not_automatic_strong_ambiguity() -> None:
    """Two weak overlapping columns should not populate near_tied strong set."""
    left = pd.DataFrame({"x": [1, 1, 2, 2], "y": [10, 20, 10, 20], "m": [1, 2, 3, 4]})
    right = pd.DataFrame({"x": [1, 1, 2, 2], "y": [10, 20, 10, 20], "n": [5, 6, 7, 8]})
    obs = build_pairwise_observation("l", left, "r", right)
    # x and y alone are not unique → not strong singleton candidates
    assert obs.key_ambiguity_observation.get("near_tied") is False
    assert obs.key_ambiguity_observation.get("plausible_singleton_count", 0) < 2


def test_dirty_whitespace_representation_normalization() -> None:
    left = pd.DataFrame({" Product ID ": ["p1"], "QTY": [1]})
    # build with cleaner names matching dirty fixtures
    left = pd.DataFrame({"Product ID": ["p1", "p2"], "QTY": [10, 20], "Amount": [100, 200]})
    right = pd.DataFrame({"product_id": ["p1", "p3"], "qty": [5, 7], "amount": [50, 70]})
    obs = build_pairwise_observation("dirty_a", left, "dirty_b", right)
    assert obs.normalized_column_name_overlap
    assert "recommended_operation" not in obs.to_dict()


def test_semantic_rename_not_automatic() -> None:
    und = {
        "file_profiles": [
            {
                "source_id": "a",
                "row_count": 1,
                "column_count": 1,
                "observations": {
                    "column_names": ["sales"],
                    "columns": [
                        {
                            "name": "sales",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 1,
                            "sample_values": [],
                        }
                    ],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "b",
                "row_count": 1,
                "column_count": 1,
                "observations": {
                    "column_names": ["amount"],
                    "columns": [
                        {
                            "name": "amount",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 1,
                            "sample_values": [],
                        }
                    ],
                },
                "semantic_hints": {},
            },
        ],
        "pairwise_observations": [],
        "relationships": [
            {
                "left_source": "a",
                "right_source": "b",
                "relationship": "compatible_schema",
                "key_candidates": [],
                "confidence": 0.5,
                "evidence": [],
                "ambiguities": [],
            }
        ],
    }
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "rename_columns",
                    "inputs": ["a"],
                    "output": "a2",
                    "params": {"mapping": {"sales": "amount"}},
                },
                {
                    "op": "union_rows",
                    "inputs": ["a2", "b"],
                    "output": "u",
                    "params": {},
                },
            ],
            "final_output": "u",
        }
    )
    # Explicit planner rename is allowed; inventing without rename is not tested here.
    result = validate_integration_plan(und, plan)
    assert result.valid


def test_failure_type_classification() -> None:
    assert classify_integration_failure_codes(["nonexistent_column"]) == FAILURE_TYPE_STRUCTURAL
    assert classify_integration_failure_codes(["ambiguous_key_selection"]) == FAILURE_TYPE_AMBIGUITY
    assert retry_mode_for_failure_type(FAILURE_TYPE_STRUCTURAL) == "repair"
    assert retry_mode_for_failure_type(FAILURE_TYPE_AMBIGUITY) == "cannot_plan_hint"


def test_phase20_unsafe_regression_ambiguous_still_blocked() -> None:
    """Capability recovery must not reopen near-tied singleton joins."""
    test_unresolved_singleton_ambiguity_still_blocked()
