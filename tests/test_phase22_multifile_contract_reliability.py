"""Phase 22: Multi-file contract reliability (LLM-free)."""

from __future__ import annotations

import pandas as pd

from core.integrate.integration_contracts import (
    FAILURE_TYPE_ALIAS,
    FAILURE_TYPE_AMBIGUITY,
    JOIN_SUFFIXES,
    classify_integration_failure_codes,
    join_output_column_names,
    resolve_aggregate_alias,
    retry_mode_for_failure_type,
)
from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.integration_pipeline import run_integration_pipeline
from core.integrate.integration_result_validate import validate_integration_result
from core.integrate.integration_validation_types import format_integration_validation_feedback


def _profile(sid: str, columns: list[dict], row_count: int = 2) -> dict:
    names = [c["name"] for c in columns]
    return {
        "source_id": sid,
        "row_count": row_count,
        "column_count": len(names),
        "observations": {"column_names": names, "columns": columns},
        "semantic_hints": {},
    }


def _col(name: str, dtype: str = "string", uniq: float = 1.0, n: int = 2) -> dict:
    return {
        "name": name,
        "dtype_family": dtype,
        "null_ratio": 0,
        "uniqueness_ratio": uniq,
        "distinct_count": n,
        "sample_values": [],
    }


def test_join_suffix_shared_contract_matches_executor() -> None:
    assert JOIN_SUFFIXES == ("_left", "_right")
    left = ["id", "status", "x"]
    right = ["id", "status", "y"]
    expected = join_output_column_names(
        left, right, left_keys=["id"], right_keys=["id"]
    )
    assert "status_left" in expected and "status_right" in expected
    assert "id" in expected

    sources = {
        "L": pd.DataFrame({"id": [1], "status": ["a"], "x": [1]}),
        "R": pd.DataFrame({"id": [1], "status": ["b"], "y": [2]}),
    }
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["L", "R"],
                    "output": "j",
                    "params": {
                        "left_keys": ["id"],
                        "right_keys": ["id"],
                        "how": "inner",
                    },
                }
            ],
            "final_output": "j",
        }
    )
    und = {
        "file_profiles": [
            _profile("L", [_col("id"), _col("status"), _col("x", "numeric")]),
            _profile("R", [_col("id"), _col("status"), _col("y", "numeric")]),
        ],
        "pairwise_observations": [
            {
                "left_source": "L",
                "right_source": "R",
                "key_candidates": [
                    {
                        "left_column": "id",
                        "right_column": "id",
                        "overlap_ratio": 1.0,
                        "left_uniqueness": 1.0,
                        "right_uniqueness": 1.0,
                    }
                ],
                "schema_similarity": 0.5,
            }
        ],
        "relationships": [
            {
                "left_source": "L",
                "right_source": "R",
                "relationship": "join_candidate",
                "confidence": 0.9,
                "evidence": [],
            }
        ],
    }
    val = validate_integration_plan(und, plan)
    assert val.valid, [e.message for e in val.errors]
    # Validator simulated schema uses _left/_right — not source__col
    join_line = next(e for e in val.lineage if e.get("op") == "join")
    assert "status_left" in (join_line.get("output_columns") or [])
    assert not any(
        str(c).startswith("R__") for c in (join_line.get("output_columns") or [])
    )

    ex = execute_integration_plan(sources, plan, val)
    assert ex.success
    assert list(ex.final_output.columns) == expected


def test_explicit_and_default_aggregate_alias_propagation() -> None:
    assert resolve_aggregate_alias({"column": "qty", "function": "sum"}) == "qty"
    assert (
        resolve_aggregate_alias(
            {"column": "qty", "function": "sum", "alias": "total_qty"}
        )
        == "total_qty"
    )
    und = {
        "file_profiles": [
            _profile(
                "u",
                [_col("sku"), _col("qty", "numeric")],
            )
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
                    "params": {"columns": ["sku", "total_qty"]},
                },
            ],
            "final_output": "out",
        }
    )
    val = validate_integration_plan(und, plan)
    assert val.valid
    sources = {"u": pd.DataFrame({"sku": ["a", "b"], "qty": [1, 2]})}
    ex = execute_integration_plan(sources, plan, val)
    assert ex.success
    rv = validate_integration_result(plan, ex, plan_validation=val)
    assert rv.valid
    assert list(ex.final_output.columns) == ["sku", "total_qty"]


def test_future_key_survival_select_then_join() -> None:
    und = {
        "file_profiles": [
            _profile("A", [_col("product_id"), _col("name")]),
            _profile("B", [_col("product_id"), _col("price", "numeric")]),
        ],
        "pairwise_observations": [
            {
                "left_source": "A",
                "right_source": "B",
                "key_candidates": [
                    {
                        "left_column": "product_id",
                        "right_column": "product_id",
                        "overlap_ratio": 1.0,
                        "left_uniqueness": 1.0,
                        "right_uniqueness": 1.0,
                    }
                ],
                "schema_similarity": 0.4,
            }
        ],
        "relationships": [
            {
                "left_source": "A",
                "right_source": "B",
                "relationship": "join_candidate",
                "confidence": 0.8,
                "evidence": [],
            }
        ],
    }
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "select_columns",
                    "inputs": ["A"],
                    "output": "a1",
                    "params": {"columns": ["name"]},  # drops product_id
                },
                {
                    "op": "join",
                    "inputs": ["a1", "B"],
                    "output": "j",
                    "params": {
                        "left_keys": ["product_id"],
                        "right_keys": ["product_id"],
                        "how": "inner",
                    },
                },
            ],
            "final_output": "j",
        }
    )
    val = validate_integration_plan(und, plan)
    assert not val.valid
    assert any(i.code in {"nonexistent_column", "missing_column"} for i in val.errors)


def test_structural_repair_feedback_no_answer_key() -> None:
    und = {
        "file_profiles": [
            _profile("u", [_col("sku"), _col("qty", "numeric")])
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
                    "params": {"columns": ["sku", "qty"]},
                },
            ],
            "final_output": "out",
        }
    )
    val = validate_integration_plan(und, plan)
    assert not val.valid
    fb = "\n".join(format_integration_validation_feedback(val, previous_plan=plan.to_dict()))
    assert "retry_mode: repair" in fb
    assert "Change qty to total_qty" not in fb
    assert "declared" in fb.lower() or "previous step" in fb.lower()


def test_union_incompatible_maps_to_cannot_plan_hint() -> None:
    assert (
        classify_integration_failure_codes(["union_incompatible_schema"])
        == FAILURE_TYPE_AMBIGUITY
    )
    assert (
        retry_mode_for_failure_type(FAILURE_TYPE_AMBIGUITY) == "cannot_plan_hint"
    )


def test_missing_metric_alias_is_alias_contract() -> None:
    assert (
        classify_integration_failure_codes(["missing_metric_output"])
        == FAILURE_TYPE_ALIAS
    )
    assert retry_mode_for_failure_type(FAILURE_TYPE_ALIAS) == "repair"


def test_unrelated_cannot_plan_contract() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "cannot_plan",
            "steps": [],
            "final_output": None,
            "reason": "sources unrelated",
            "ambiguities": ["unrelated"],
        }
    )
    und = {
        "file_profiles": [
            _profile("A", [_col("x")]),
            _profile("B", [_col("y")]),
        ],
        "pairwise_observations": [],
        "relationships": [
            {
                "left_source": "A",
                "right_source": "B",
                "relationship": "unrelated",
                "confidence": 0.9,
                "evidence": [],
            }
        ],
    }
    val = validate_integration_plan(und, plan)
    assert val.valid
    assert plan.status == "cannot_plan"
    assert plan.steps == []
    assert plan.final_output is None


def test_repeated_alias_contract_detected_in_pipeline() -> None:
    und = {
        "file_profiles": [
            _profile("u", [_col("sku"), _col("qty", "numeric")])
        ],
        "pairwise_observations": [],
        "relationships": [],
    }
    bad_plan = {
        "status": "planned",
        "steps": [
            {
                "id": "step_1",
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
                "id": "step_2",
                "op": "select_columns",
                "inputs": ["agg"],
                "output": "out",
                "params": {"columns": ["sku", "qty"]},
            },
        ],
        "final_output": "out",
    }

    def build_plan_fn(*_a, **_k):
        return integration_plan_from_dict(bad_plan)

    result = run_integration_pipeline(
        "sum qty by sku",
        {"u": pd.DataFrame({"sku": ["a"], "qty": [1]})},
        und,
        max_retries=2,
        build_plan_fn=build_plan_fn,
    )
    assert result.status == "failed"
    assert result.metadata.get("repeated_contract_failure") is True
    assert any(
        e.get("repeated_structural_contract_failure") for e in result.retry_log
    )


def test_near_tie_join_still_blocked() -> None:
    """Phase 20/21 regression: observational near-tied singleton must not execute."""
    from core.integrate.relationship_profile import build_pairwise_observation

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
            _profile(
                "ambiguous_a",
                [_col(c) for c in a.columns],
                row_count=4,
            ),
            _profile(
                "ambiguous_b",
                [
                    _col(c, "numeric" if c == "score" else "string")
                    for c in b.columns
                ],
                row_count=4,
            ),
        ],
        "pairwise_observations": [obs.to_dict()],
        "relationships": [
            {
                "left_source": "ambiguous_a",
                "right_source": "ambiguous_b",
                "relationship": "join_candidate",
                "confidence": 0.8,
                "evidence": [],
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
    val = validate_integration_plan(und, plan)
    assert not val.valid
    assert any(i.code == "ambiguous_key_selection" for i in val.errors)


def test_three_file_suffix_alias_chain_schema() -> None:
    und = {
        "file_profiles": [
            _profile("A", [_col("id"), _col("status"), _col("amt", "numeric")]),
            _profile("B", [_col("id"), _col("status"), _col("cat")]),
            _profile("C", [_col("cat"), _col("label")]),
        ],
        "pairwise_observations": [
            {
                "left_source": "A",
                "right_source": "B",
                "key_candidates": [
                    {
                        "left_column": "id",
                        "right_column": "id",
                        "overlap_ratio": 1.0,
                        "left_uniqueness": 1.0,
                        "right_uniqueness": 1.0,
                    }
                ],
                "schema_similarity": 0.5,
            },
            {
                "left_source": "B",
                "right_source": "C",
                "key_candidates": [
                    {
                        "left_column": "cat",
                        "right_column": "cat",
                        "overlap_ratio": 1.0,
                        "left_uniqueness": 1.0,
                        "right_uniqueness": 1.0,
                    }
                ],
                "schema_similarity": 0.3,
            },
        ],
        "relationships": [
            {
                "left_source": "A",
                "right_source": "B",
                "relationship": "join_candidate",
                "confidence": 0.9,
                "evidence": [],
            },
            {
                "left_source": "B",
                "right_source": "C",
                "relationship": "lookup_candidate",
                "confidence": 0.8,
                "evidence": [],
            },
        ],
    }
    # Downstream wrongly references bare `status` after collision → error
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["A", "B"],
                    "output": "ab",
                    "params": {
                        "left_keys": ["id"],
                        "right_keys": ["id"],
                        "how": "inner",
                    },
                },
                {
                    "op": "select_columns",
                    "inputs": ["ab"],
                    "output": "out",
                    "params": {"columns": ["id", "status", "amt"]},
                },
            ],
            "final_output": "out",
        }
    )
    val = validate_integration_plan(und, plan)
    assert not val.valid
    assert any(i.code == "nonexistent_column" for i in val.errors)
    # Correct suffixed select should validate
    plan2 = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["A", "B"],
                    "output": "ab",
                    "params": {
                        "left_keys": ["id"],
                        "right_keys": ["id"],
                        "how": "inner",
                    },
                },
                {
                    "op": "select_columns",
                    "inputs": ["ab"],
                    "output": "out",
                    "params": {"columns": ["id", "status_left", "amt"]},
                },
            ],
            "final_output": "out",
        }
    )
    val2 = validate_integration_plan(und, plan2)
    assert val2.valid, [e.message for e in val2.errors]
