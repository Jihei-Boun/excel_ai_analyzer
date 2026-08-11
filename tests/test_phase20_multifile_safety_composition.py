"""Phase 20: Multi-file safety & composition reliability (LLM-free)."""

from __future__ import annotations

import pandas as pd

from core.integrate.integration_pipeline import run_integration_pipeline
from core.integrate.integration_plan_types import (
    integration_operation_family_signature,
    integration_plan_from_dict,
    repeated_integration_family_feedback,
)
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.relationship_profile import build_pairwise_observation
def _ambig_frames():
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
    return a, b


def _ambig_understanding() -> dict:
    a, b = _ambig_frames()
    obs = build_pairwise_observation("ambiguous_a", a, "ambiguous_b", b)
    return {
        "file_profiles": [
            {
                "source_id": "ambiguous_a",
                "row_count": len(a),
                "column_count": 3,
                "observations": {
                    "column_names": list(a.columns),
                    "columns": [
                        {
                            "name": c,
                            "dtype_family": "string" if c != "score" else "numeric",
                            "null_ratio": 0.0,
                            "uniqueness_ratio": 1.0,
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
                "row_count": len(b),
                "column_count": 3,
                "observations": {
                    "column_names": list(b.columns),
                    "columns": [
                        {
                            "name": c,
                            "dtype_family": "numeric" if c == "score" else "string",
                            "null_ratio": 0.0,
                            "uniqueness_ratio": 1.0,
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
                # Even if LLM overconfidently labels join_candidate, observation wins
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


def test_composite_parts_not_singleton_ambiguity() -> None:
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
    amb = obs.key_ambiguity_observation
    assert amb.get("near_tied") is False
    assert amb.get("plausible_singleton_count", 0) < 2
    assert any(
        set(c.get("left_columns") or []) == {"store_id", "product_id"} for c in obs.composite_key_observations
    )


def test_ambiguous_candidate_evidence_near_tied() -> None:
    a, b = _ambig_frames()
    obs = build_pairwise_observation("a", a, "b", b)
    amb = obs.key_ambiguity_observation
    assert amb.get("near_tied") is True
    assert amb.get("plausible_singleton_count", 0) >= 2
    assert len(amb.get("tied_pairs") or []) >= 2
    assert "recommended_operation" not in obs.to_dict()
    assert "best_join_key" not in obs.to_dict()


def test_ambiguous_join_blocked_even_if_label_join_candidate() -> None:
    und = _ambig_understanding()
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
    assert not result.valid
    assert any(i.code == "ambiguous_key_selection" for i in result.errors)


def test_composite_key_not_mistaken_for_ambiguity() -> None:
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
    # Singles are not unique → may look weak; composite should be observed
    comps = obs.composite_key_observations
    assert any(
        set(c.get("left_columns") or []) == {"store_id", "product_id"}
        and float(c.get("left_uniqueness") or 0) >= 0.98
        for c in comps
    )
    # Composite join must not be blocked as ambiguous singleton
    und = {
        "file_profiles": [
            {
                "source_id": "inv",
                "row_count": 4,
                "column_count": 3,
                "observations": {
                    "column_names": list(left.columns),
                    "columns": [
                        {
                            "name": "store_id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 0.5,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                        {
                            "name": "product_id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 0.5,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                        {
                            "name": "qty",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 4,
                            "sample_values": [],
                        },
                    ],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "price",
                "row_count": 4,
                "column_count": 3,
                "observations": {
                    "column_names": list(right.columns),
                    "columns": [
                        {
                            "name": "store_id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 0.5,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                        {
                            "name": "product_id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 0.5,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                        {
                            "name": "price",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 4,
                            "sample_values": [],
                        },
                    ],
                },
                "semantic_hints": {},
            },
        ],
        "pairwise_observations": [obs.to_dict()],
        "relationships": [
            {
                "left_source": "inv",
                "right_source": "price",
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
                    "inputs": ["inv", "price"],
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


def test_relationship_candidate_not_forced_join() -> None:
    """join_candidate label alone must not force join when keys near-tied."""
    und = _ambig_understanding()
    assert und["relationships"][0]["relationship"] == "join_candidate"
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["ambiguous_a", "ambiguous_b"],
                    "output": "j",
                    "params": {
                        "left_keys": ["account_id"],
                        "right_keys": ["account_id"],
                        "how": "left",
                    },
                }
            ],
            "final_output": "j",
        }
    )
    result = validate_integration_plan(und, plan)
    assert any(i.code == "ambiguous_key_selection" for i in result.errors)


def test_union_then_aggregate_family_signature() -> None:
    plan = {
        "status": "planned",
        "steps": [
            {"op": "union_rows", "inputs": ["a", "b"], "output": "u", "params": {}},
            {
                "op": "aggregate",
                "inputs": ["u"],
                "output": "agg",
                "params": {
                    "group_by": ["entity"],
                    "metrics": [{"column": "metric", "function": "sum"}],
                },
            },
        ],
        "final_output": "agg",
    }
    assert integration_operation_family_signature(plan) == "union_then_aggregate"


def test_filter_union_aggregate_family_signature() -> None:
    plan = {
        "status": "planned",
        "steps": [
            {
                "op": "filter_rows",
                "inputs": ["a"],
                "output": "af",
                "params": {
                    "conditions": [{"column": "x", "operator": "eq", "value": 1}],
                },
            },
            {
                "op": "filter_rows",
                "inputs": ["b"],
                "output": "bf",
                "params": {
                    "conditions": [{"column": "x", "operator": "eq", "value": 1}],
                },
            },
            {"op": "union_rows", "inputs": ["af", "bf"], "output": "u", "params": {}},
            {
                "op": "aggregate",
                "inputs": ["u"],
                "output": "agg",
                "params": {
                    "group_by": ["g"],
                    "metrics": [{"column": "v", "function": "sum"}],
                },
            },
        ],
        "final_output": "agg",
    }
    assert integration_operation_family_signature(plan) == "filter_union_aggregate"


def test_join_then_aggregate_family_signature() -> None:
    plan = {
        "status": "planned",
        "steps": [
            {
                "op": "join",
                "inputs": ["a", "b"],
                "output": "j",
                "params": {
                    "left_keys": ["id"],
                    "right_keys": ["id"],
                    "how": "left",
                },
            },
            {
                "op": "aggregate",
                "inputs": ["j"],
                "output": "agg",
                "params": {
                    "group_by": ["region"],
                    "metrics": [{"column": "amount", "function": "sum"}],
                },
            },
        ],
        "final_output": "agg",
    }
    assert integration_operation_family_signature(plan) == "join_then_aggregate"


def test_three_file_dependency_chain_family() -> None:
    plan = {
        "status": "planned",
        "steps": [
            {
                "op": "join",
                "inputs": ["a", "b"],
                "output": "ab",
                "params": {
                    "left_keys": ["k"],
                    "right_keys": ["k"],
                    "how": "left",
                },
            },
            {
                "op": "join",
                "inputs": ["ab", "c"],
                "output": "abc",
                "params": {
                    "left_keys": ["k2"],
                    "right_keys": ["k2"],
                    "how": "left",
                },
            },
            {
                "op": "aggregate",
                "inputs": ["abc"],
                "output": "agg",
                "params": {
                    "group_by": ["g"],
                    "metrics": [{"column": "v", "function": "sum"}],
                },
            },
        ],
        "final_output": "agg",
    }
    assert integration_operation_family_signature(plan) == "multi_join_then_aggregate"
    parsed = integration_plan_from_dict(plan)
    assert parsed.final_output == "agg"
    assert [s.op for s in parsed.steps] == ["join", "join", "aggregate"]


def test_dirty_whitespace_safety_normalization_observation() -> None:
    left = pd.DataFrame({"Customer ID": ["C1", "C2"], "amount": [1, 2]})
    right = pd.DataFrame({"customer_id": ["C1", "C2"], "region": ["x", "y"]})
    obs = build_pairwise_observation("l", left, "r", right)
    # Representation overlap via normalized names — observation only
    assert obs.normalized_column_name_overlap
    assert "recommended_operation" not in obs.to_dict()


def test_semantic_rename_not_automatic() -> None:
    """Validator/Executor must not invent rename from similar names."""
    und = {
        "file_profiles": [
            {
                "source_id": "a",
                "row_count": 2,
                "column_count": 2,
                "observations": {
                    "column_names": ["cust id", "amount"],
                    "columns": [
                        {
                            "name": "cust id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                        {
                            "name": "amount",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                    ],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "b",
                "row_count": 2,
                "column_count": 2,
                "observations": {
                    "column_names": ["customer_id", "region"],
                    "columns": [
                        {
                            "name": "customer_id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                        {
                            "name": "region",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
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
                "relationship": "join_candidate",
                "key_candidates": [],
                "confidence": 0.5,
                "evidence": [],
                "ambiguities": [],
            }
        ],
    }
    # Join without explicit rename using non-existent customer_id on left
    plan = integration_plan_from_dict(
        {
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
            "final_output": "j",
        }
    )
    result = validate_integration_plan(und, plan)
    assert any(i.code == "nonexistent_column" for i in result.errors)


def test_repeated_integration_family_detection() -> None:
    join_only = {
        "status": "planned",
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
            }
        ],
        "final_output": "j",
    }
    assert integration_operation_family_signature(join_only) == "join_only"
    lines = repeated_integration_family_feedback("join_only")
    joined = "\n".join(lines)
    assert "repeated_integration_family" in joined
    assert "materially different" in joined.lower()
    assert "customer_id" not in joined
    assert "union_rows" not in joined
    assert "left join" not in joined.lower()


def test_retry_diversity_feedback_and_safe_cannot_plan() -> None:
    """Repeated unsafe ambiguous join → cannot_plan without prescribing keys."""
    und = _ambig_understanding()
    a, b = _ambig_frames()
    sources = {"ambiguous_a": a, "ambiguous_b": b}
    calls = {"n": 0}

    def chat_json(_prompt: str, **_kwargs):
        calls["n"] += 1
        # Always propose same ambiguous join strategy
        return {
            "status": "planned",
            "steps": [
                {
                    "id": "step_1",
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
            "reason": None,
            "ambiguities": [],
            "notes": [],
        }

    result = run_integration_pipeline(
        "두 파일을 연결해줘",
        sources,
        und,
        chat_json_fn=chat_json,
        max_retries=2,
    )
    assert result.status in {"cannot_plan", "failed"}
    assert result.status != "success"
    assert result.metadata.get("validator_blocked_unsafe_plan") is True
    # Family diversity / duplicate detection should appear in retry log
    codes = [c for e in result.retry_log for c in (e.get("failure_codes") or [])]
    assert "ambiguous_key_selection" in codes or any(
        "repeated" in c for c in codes
    )


def test_validator_false_positive_regression_master_detail() -> None:
    """Clear one-to-many join must still validate."""
    und = {
        "file_profiles": [
            {
                "source_id": "customers",
                "row_count": 3,
                "column_count": 2,
                "observations": {
                    "column_names": ["customer_id", "name"],
                    "columns": [
                        {
                            "name": "customer_id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1.0,
                            "distinct_count": 3,
                            "sample_values": [],
                        },
                        {
                            "name": "name",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 3,
                            "sample_values": [],
                        },
                    ],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "orders",
                "row_count": 4,
                "column_count": 3,
                "observations": {
                    "column_names": ["order_id", "customer_id", "amount"],
                    "columns": [
                        {
                            "name": "order_id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 4,
                            "sample_values": [],
                        },
                        {
                            "name": "customer_id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 0.75,
                            "distinct_count": 3,
                            "sample_values": [],
                        },
                        {
                            "name": "amount",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 4,
                            "sample_values": [],
                        },
                    ],
                },
                "semantic_hints": {},
            },
        ],
        "pairwise_observations": [
            {
                "left_source": "customers",
                "right_source": "orders",
                "schema_similarity": 0.2,
                "exact_column_name_overlap": ["customer_id"],
                "candidate_pairs": [
                    {
                        "left_column": "customer_id",
                        "right_column": "customer_id",
                        "dtype_compatible": True,
                        "name_similarity": 1.0,
                        "value_overlap_ratio": 1.0,
                        "left_uniqueness": 1.0,
                        "right_uniqueness": 0.75,
                        "cardinality_evidence": "one_to_many",
                    }
                ],
                "key_ambiguity_observation": {
                    "near_tied": False,
                    "plausible_singleton_count": 1,
                    "evidence_gap": None,
                    "tied_pairs": [],
                },
                "composite_key_observations": [],
            }
        ],
        "relationships": [
            {
                "left_source": "customers",
                "right_source": "orders",
                "relationship": "master_detail_candidate",
                "key_candidates": [
                    {"left_column": "customer_id", "right_column": "customer_id"}
                ],
                "confidence": 0.9,
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
    result = validate_integration_plan(und, plan)
    assert result.valid


def test_compatible_union_not_false_positive() -> None:
    und = {
        "file_profiles": [
            {
                "source_id": "jan",
                "row_count": 2,
                "column_count": 2,
                "observations": {
                    "column_names": ["product", "amount"],
                    "columns": [
                        {
                            "name": "product",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                        {
                            "name": "amount",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                    ],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "feb",
                "row_count": 2,
                "column_count": 2,
                "observations": {
                    "column_names": ["product", "amount"],
                    "columns": [
                        {
                            "name": "product",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                        {
                            "name": "amount",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [],
                        },
                    ],
                },
                "semantic_hints": {},
            },
        ],
        "pairwise_observations": [
            {
                "left_source": "jan",
                "right_source": "feb",
                "schema_similarity": 1.0,
                "exact_column_name_overlap": ["product", "amount"],
                "candidate_pairs": [],
                "key_ambiguity_observation": {"near_tied": False, "tied_pairs": []},
                "composite_key_observations": [],
            }
        ],
        "relationships": [
            {
                "left_source": "jan",
                "right_source": "feb",
                "relationship": "same_schema",
                "key_candidates": [],
                "confidence": 0.9,
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
                    "op": "union_rows",
                    "inputs": ["jan", "feb"],
                    "output": "u",
                    "params": {"column_policy": "aligned"},
                }
            ],
            "final_output": "u",
        }
    )
    result = validate_integration_plan(und, plan)
    assert result.valid
