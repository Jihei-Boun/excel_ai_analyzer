"""Phase 16: IntegrationPlan Validator tests."""

from __future__ import annotations

import copy

from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.integration_validation_types import format_integration_validation_feedback


def _profiles_same_schema():
    return {
        "file_profiles": [
            {
                "source_id": "sales_jan",
                "row_count": 10,
                "column_count": 2,
                "observations": {
                    "column_names": ["product", "qty"],
                    "columns": [
                        {
                            "name": "product",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 10,
                            "sample_values": ["A"],
                        },
                        {
                            "name": "qty",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 0.8,
                            "distinct_count": 8,
                            "sample_values": [1],
                        },
                    ],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "sales_feb",
                "row_count": 12,
                "column_count": 2,
                "observations": {
                    "column_names": ["product", "qty"],
                    "columns": [
                        {
                            "name": "product",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 12,
                            "sample_values": ["B"],
                        },
                        {
                            "name": "qty",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 0.7,
                            "distinct_count": 8,
                            "sample_values": [2],
                        },
                    ],
                },
                "semantic_hints": {},
            },
        ],
        "pairwise_observations": [
            {
                "left_source": "sales_jan",
                "right_source": "sales_feb",
                "schema_similarity": 1.0,
                "exact_column_name_overlap": ["product", "qty"],
                "candidate_pairs": [],
            }
        ],
        "relationships": [
            {
                "left_source": "sales_jan",
                "right_source": "sales_feb",
                "relationship": "same_schema",
                "key_candidates": [],
                "confidence": 0.9,
                "evidence": [],
                "ambiguities": [],
            }
        ],
    }


def _profiles_master_detail():
    return {
        "file_profiles": [
            {
                "source_id": "customers",
                "row_count": 100,
                "column_count": 2,
                "observations": {
                    "column_names": ["customer_id", "name"],
                    "columns": [
                        {
                            "name": "customer_id",
                            "dtype_family": "string",
                            "null_ratio": 0.0,
                            "uniqueness_ratio": 1.0,
                            "distinct_count": 100,
                            "sample_values": ["C1"],
                        },
                        {
                            "name": "name",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 100,
                            "sample_values": ["A"],
                        },
                    ],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "orders",
                "row_count": 400,
                "column_count": 3,
                "observations": {
                    "column_names": ["order_id", "customer_id", "amount"],
                    "columns": [
                        {
                            "name": "order_id",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1.0,
                            "distinct_count": 400,
                            "sample_values": [1],
                        },
                        {
                            "name": "customer_id",
                            "dtype_family": "string",
                            "null_ratio": 0.0,
                            "uniqueness_ratio": 0.25,
                            "distinct_count": 100,
                            "sample_values": ["C1"],
                        },
                        {
                            "name": "amount",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 0.9,
                            "distinct_count": 360,
                            "sample_values": [10],
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
                        "value_overlap_ratio": 0.95,
                        "left_uniqueness": 1.0,
                        "right_uniqueness": 0.25,
                        "cardinality_evidence": "one_to_many",
                    }
                ],
            }
        ],
        "relationships": [
            {
                "left_source": "customers",
                "right_source": "orders",
                "relationship": "master_detail_candidate",
                "key_candidates": [
                    {
                        "left_column": "customer_id",
                        "right_column": "customer_id",
                        "confidence": 0.9,
                    }
                ],
                "confidence": 0.88,
                "evidence": [],
                "ambiguities": [],
            }
        ],
    }


def test_valid_union() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "union_rows",
                    "inputs": ["sales_jan", "sales_feb"],
                    "output": "all",
                    "params": {},
                }
            ],
            "final_output": "all",
        }
    )
    result = validate_integration_plan(_profiles_same_schema(), plan)
    assert result.valid
    assert not result.errors


def test_valid_one_to_many_join() -> None:
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
                        "how": "left",
                    },
                }
            ],
            "final_output": "joined",
        }
    )
    result = validate_integration_plan(_profiles_master_detail(), plan)
    assert result.valid
    assert any(i.code == "join_cardinality" for i in result.infos)


def test_valid_one_to_one_join() -> None:
    und = {
        "file_profiles": [
            {
                "source_id": "a",
                "row_count": 50,
                "column_count": 1,
                "observations": {
                    "columns": [
                        {
                            "name": "id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1.0,
                            "distinct_count": 50,
                            "sample_values": ["1"],
                        }
                    ],
                    "column_names": ["id"],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "b",
                "row_count": 50,
                "column_count": 2,
                "observations": {
                    "columns": [
                        {
                            "name": "id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1.0,
                            "distinct_count": 50,
                            "sample_values": ["1"],
                        },
                        {
                            "name": "val",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 50,
                            "sample_values": [1],
                        },
                    ],
                    "column_names": ["id", "val"],
                },
                "semantic_hints": {},
            },
        ],
        "pairwise_observations": [
            {
                "left_source": "a",
                "right_source": "b",
                "candidate_pairs": [
                    {
                        "left_column": "id",
                        "right_column": "id",
                        "value_overlap_ratio": 1.0,
                        "left_uniqueness": 1.0,
                        "right_uniqueness": 1.0,
                        "cardinality_evidence": "one_to_one",
                    }
                ],
            }
        ],
        "relationships": [
            {
                "left_source": "a",
                "right_source": "b",
                "relationship": "join_candidate",
                "key_candidates": [{"left_column": "id", "right_column": "id"}],
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
                    "inputs": ["a", "b"],
                    "output": "j",
                    "params": {"left_keys": ["id"], "right_keys": ["id"], "how": "inner"},
                }
            ],
            "final_output": "j",
        }
    )
    assert validate_integration_plan(und, plan).valid


def test_valid_union_then_aggregate() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "union_rows",
                    "inputs": ["sales_jan", "sales_feb"],
                    "output": "u",
                    "params": {},
                },
                {
                    "op": "aggregate",
                    "inputs": ["u"],
                    "output": "s",
                    "params": {
                        "group_by": ["product"],
                        "metrics": [{"column": "qty", "function": "sum"}],
                    },
                },
            ],
            "final_output": "s",
        }
    )
    assert validate_integration_plan(_profiles_same_schema(), plan).valid


def test_valid_join_then_select() -> None:
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
                        "how": "left",
                    },
                },
                {
                    "op": "select_columns",
                    "inputs": ["joined"],
                    "output": "final",
                    "params": {"columns": ["customer_id", "name", "amount"]},
                },
            ],
            "final_output": "final",
        }
    )
    assert validate_integration_plan(_profiles_master_detail(), plan).valid


def test_cannot_plan_is_valid_safe_outcome() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "cannot_plan",
            "steps": [],
            "final_output": None,
            "reason": "unrelated",
            "ambiguities": [],
        }
    )
    result = validate_integration_plan(_profiles_same_schema(), plan)
    assert result.valid
    assert any(i.code == "cannot_plan_accepted" for i in result.infos)


def test_nonexistent_input() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "union_rows",
                    "inputs": ["sales_jan", "missing_file"],
                    "output": "u",
                    "params": {},
                }
            ],
            "final_output": "u",
        }
    )
    result = validate_integration_plan(_profiles_same_schema(), plan)
    assert not result.valid
    assert any(i.code == "nonexistent_input" for i in result.errors)


def test_nonexistent_column() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "select_columns",
                    "inputs": ["sales_jan"],
                    "output": "s",
                    "params": {"columns": ["nope"]},
                }
            ],
            "final_output": "s",
        }
    )
    result = validate_integration_plan(_profiles_same_schema(), plan)
    assert any(i.code == "nonexistent_column" for i in result.errors)


def test_duplicate_step_id() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "id": "same",
                    "op": "select_columns",
                    "inputs": ["sales_jan"],
                    "output": "a",
                    "params": {"columns": ["product"]},
                },
                {
                    "id": "same",
                    "op": "select_columns",
                    "inputs": ["sales_feb"],
                    "output": "b",
                    "params": {"columns": ["product"]},
                },
            ],
            "final_output": "b",
        }
    )
    result = validate_integration_plan(_profiles_same_schema(), plan)
    assert any(i.code == "duplicate_step_id" for i in result.errors)


def test_unresolved_dependency() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "aggregate",
                    "inputs": ["not_yet"],
                    "output": "s",
                    "params": {
                        "group_by": ["product"],
                        "metrics": [{"column": "qty", "function": "sum"}],
                    },
                }
            ],
            "final_output": "s",
        }
    )
    result = validate_integration_plan(_profiles_same_schema(), plan)
    assert any(i.code == "nonexistent_input" for i in result.errors)


def test_unsupported_operation_via_dict_bypass() -> None:
    # Force unsupported op past parser using raw validate path after forging plan object
    from core.integrate.integration_plan_types import IntegrationPlan, IntegrationStep

    plan = IntegrationPlan(
        status="planned",
        steps=[
            IntegrationStep(
                id="s1",
                op="pivot_magic",
                inputs=["sales_jan"],
                output="x",
                params={},
            )
        ],
        final_output="x",
    )
    result = validate_integration_plan(_profiles_same_schema(), plan)
    assert any(i.code == "unsupported_operation" for i in result.errors)


def test_many_to_many_dangerous_join() -> None:
    und = {
        "file_profiles": [
            {
                "source_id": "l",
                "row_count": 1000,
                "column_count": 1,
                "observations": {
                    "columns": [
                        {
                            "name": "k",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 0.2,
                            "distinct_count": 200,
                            "sample_values": ["a"],
                        }
                    ],
                    "column_names": ["k"],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "r",
                "row_count": 1000,
                "column_count": 1,
                "observations": {
                    "columns": [
                        {
                            "name": "k",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 0.22,
                            "distinct_count": 220,
                            "sample_values": ["a"],
                        }
                    ],
                    "column_names": ["k"],
                },
                "semantic_hints": {},
            },
        ],
        "pairwise_observations": [
            {
                "left_source": "l",
                "right_source": "r",
                "candidate_pairs": [
                    {
                        "left_column": "k",
                        "right_column": "k",
                        "value_overlap_ratio": 0.9,
                        "left_uniqueness": 0.2,
                        "right_uniqueness": 0.22,
                        "cardinality_evidence": "many_to_many",
                    }
                ],
            }
        ],
        "relationships": [
            {
                "left_source": "l",
                "right_source": "r",
                "relationship": "join_candidate",
                "key_candidates": [{"left_column": "k", "right_column": "k"}],
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
                    "inputs": ["l", "r"],
                    "output": "j",
                    "params": {"left_keys": ["k"], "right_keys": ["k"], "how": "inner"},
                }
            ],
            "final_output": "j",
        }
    )
    result = validate_integration_plan(und, plan)
    assert any(i.code == "many_to_many_join_risk" for i in result.errors)


def test_extreme_amplification() -> None:
    und = {
        "file_profiles": [
            {
                "source_id": "l",
                "row_count": 10000,
                "column_count": 1,
                "observations": {
                    "columns": [
                        {
                            "name": "k",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 0.05,
                            "distinct_count": 500,
                            "sample_values": ["x"],
                        }
                    ],
                    "column_names": ["k"],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "r",
                "row_count": 8000,
                "column_count": 1,
                "observations": {
                    "columns": [
                        {
                            "name": "k",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 0.05,
                            "distinct_count": 400,
                            "sample_values": ["x"],
                        }
                    ],
                    "column_names": ["k"],
                },
                "semantic_hints": {},
            },
        ],
        "pairwise_observations": [
            {
                "left_source": "l",
                "right_source": "r",
                "candidate_pairs": [
                    {
                        "left_column": "k",
                        "right_column": "k",
                        "value_overlap_ratio": 1.0,
                        "left_uniqueness": 0.05,
                        "right_uniqueness": 0.05,
                        "cardinality_evidence": "many_to_many",
                    }
                ],
            }
        ],
        "relationships": [
            {
                "left_source": "l",
                "right_source": "r",
                "relationship": "join_candidate",
                "key_candidates": [],
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
                    "inputs": ["l", "r"],
                    "output": "j",
                    "params": {"left_keys": ["k"], "right_keys": ["k"], "how": "inner"},
                }
            ],
            "final_output": "j",
        }
    )
    result = validate_integration_plan(und, plan)
    codes = {i.code for i in result.errors}
    assert "many_to_many_join_risk" in codes or "extreme_row_amplification" in codes


def test_union_incompatible_schema() -> None:
    und = {
        "file_profiles": [
            {
                "source_id": "a",
                "row_count": 5,
                "column_count": 2,
                "observations": {
                    "column_names": ["customer_id", "amount"],
                    "columns": [
                        {
                            "name": "customer_id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 5,
                            "sample_values": ["1"],
                        },
                        {
                            "name": "amount",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 5,
                            "sample_values": [1],
                        },
                    ],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "b",
                "row_count": 5,
                "column_count": 2,
                "observations": {
                    "column_names": ["customer_id", "name"],
                    "columns": [
                        {
                            "name": "customer_id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 5,
                            "sample_values": ["1"],
                        },
                        {
                            "name": "name",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 5,
                            "sample_values": ["x"],
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
                "relationship": "partial_overlap",
                "key_candidates": [],
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
                    "inputs": ["a", "b"],
                    "output": "u",
                    "params": {"column_policy": "aligned"},
                }
            ],
            "final_output": "u",
        }
    )
    result = validate_integration_plan(und, plan)
    assert any(i.code == "union_incompatible_schema" for i in result.errors)


def test_aggregate_string_sum() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "aggregate",
                    "inputs": ["sales_jan"],
                    "output": "s",
                    "params": {
                        "group_by": ["product"],
                        "metrics": [{"column": "product", "function": "sum"}],
                    },
                }
            ],
            "final_output": "s",
        }
    )
    result = validate_integration_plan(_profiles_same_schema(), plan)
    assert any(i.code == "aggregate_non_numeric" for i in result.errors)


def test_aggregate_missing_group_column() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "aggregate",
                    "inputs": ["sales_jan"],
                    "output": "s",
                    "params": {
                        "group_by": ["missing"],
                        "metrics": [{"column": "qty", "function": "sum"}],
                    },
                }
            ],
            "final_output": "s",
        }
    )
    result = validate_integration_plan(_profiles_same_schema(), plan)
    assert any(i.code == "nonexistent_column" for i in result.errors)


def test_select_removes_join_key() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "select_columns",
                    "inputs": ["customers"],
                    "output": "c2",
                    "params": {"columns": ["name"]},
                },
                {
                    "op": "join",
                    "inputs": ["c2", "orders"],
                    "output": "j",
                    "params": {
                        "left_keys": ["customer_id"],
                        "right_keys": ["customer_id"],
                        "how": "left",
                    },
                },
            ],
            "final_output": "j",
        }
    )
    result = validate_integration_plan(_profiles_master_detail(), plan)
    assert any(i.code == "nonexistent_column" for i in result.errors)


def test_ambiguous_relationship_forced_join() -> None:
    und = _profiles_master_detail()
    und["relationships"] = [
        {
            "left_source": "customers",
            "right_source": "orders",
            "relationship": "ambiguous",
            "key_candidates": [
                {"left_column": "customer_id", "right_column": "customer_id"},
                {"left_column": "name", "right_column": "customer_id"},
            ],
            "ambiguities": ["customer_id vs name"],
            "confidence": 0.4,
            "evidence": [],
        }
    ]
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
    result = validate_integration_plan(und, plan)
    assert any(i.code == "ambiguous_key_selection" for i in result.errors)


def test_insufficient_evidence_forced_join() -> None:
    und = _profiles_master_detail()
    und["relationships"] = [
        {
            "left_source": "customers",
            "right_source": "orders",
            "relationship": "insufficient_evidence",
            "key_candidates": [],
            "ambiguities": [],
            "confidence": 0.1,
            "evidence": [],
        }
    ]
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
    result = validate_integration_plan(und, plan)
    assert any(i.code == "insufficient_evidence_forced_join" for i in result.errors)


def test_unrelated_forced_join() -> None:
    und = _profiles_master_detail()
    und["relationships"] = [
        {
            "left_source": "customers",
            "right_source": "orders",
            "relationship": "unrelated",
            "key_candidates": [],
            "ambiguities": [],
            "evidence": [],
        }
    ]
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
    result = validate_integration_plan(und, plan)
    assert any(i.code == "join_against_unrelated" for i in result.errors)


def test_missing_final_output_caught() -> None:
    from core.integrate.integration_plan_types import IntegrationPlan, IntegrationStep

    plan = IntegrationPlan(
        status="planned",
        steps=[
            IntegrationStep(
                id="s1",
                op="union_rows",
                inputs=["sales_jan", "sales_feb"],
                output="u",
                params={"column_policy": "aligned"},
            )
        ],
        final_output=None,
    )
    result = validate_integration_plan(_profiles_same_schema(), plan)
    assert any(i.code == "missing_final_output" for i in result.errors)


def test_plan_immutability() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "union_rows",
                    "inputs": ["sales_jan", "sales_feb"],
                    "output": "all",
                    "params": {},
                }
            ],
            "final_output": "all",
        }
    )
    before = copy.deepcopy(plan.to_dict())
    validate_integration_plan(_profiles_same_schema(), plan)
    assert plan.to_dict() == before


def test_validator_does_not_choose_keys_or_rewrite_ops() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["customers", "orders"],
                    "output": "j",
                    "params": {
                        "left_keys": ["name"],
                        "right_keys": ["customer_id"],
                        "how": "inner",
                    },
                }
            ],
            "final_output": "j",
        }
    )
    before = copy.deepcopy(plan.to_dict())
    result = validate_integration_plan(_profiles_master_detail(), plan)
    assert plan.to_dict() == before
    # Still the same keys — no autocomplete to customer_id
    assert plan.steps[0].params["left_keys"] == ["name"]
    assert plan.steps[0].op == "join"
    # Feedback must not prescribe a replacement key
    fb = "\n".join(format_integration_validation_feedback(result, previous_plan=before))
    assert "Use customer_id" not in fb


def test_high_null_join_key_warning() -> None:
    und = _profiles_master_detail()
    for c in und["file_profiles"][1]["observations"]["columns"]:
        if c["name"] == "customer_id":
            c["null_ratio"] = 0.4
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
    assert result.valid or not result.valid  # may still be valid with warning
    assert any(i.code in {"high_null_join_key", "null_join_key"} for i in result.warnings)


def test_low_overlap_inner_join_warning() -> None:
    und = _profiles_master_detail()
    und["pairwise_observations"][0]["candidate_pairs"][0]["value_overlap_ratio"] = 0.05
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
    result = validate_integration_plan(und, plan)
    assert any(i.code == "low_key_overlap" for i in result.warnings)


def test_subtotal_double_count_warning() -> None:
    und = _profiles_same_schema()
    und["file_profiles"][0]["observations"]["columns"][0]["sample_values"] = ["합계"]
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "aggregate",
                    "inputs": ["sales_jan"],
                    "output": "s",
                    "params": {
                        "group_by": ["product"],
                        "metrics": [{"column": "qty", "function": "sum"}],
                    },
                }
            ],
            "final_output": "s",
        }
    )
    result = validate_integration_plan(und, plan)
    assert any(i.code == "possible_subtotal_double_count" for i in result.warnings)


def test_feedback_format_for_retry() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "union_rows",
                    "inputs": ["sales_jan", "nope"],
                    "output": "u",
                    "params": {},
                }
            ],
            "final_output": "u",
        }
    )
    result = validate_integration_plan(_profiles_same_schema(), plan)
    lines = format_integration_validation_feedback(result)
    assert any("Failure stage: integration_plan_validation" in x for x in lines)
    assert any("Do not automatically invent" in x for x in lines)


def test_lineage_metadata_present() -> None:
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
                        "how": "left",
                    },
                }
            ],
            "final_output": "joined",
        }
    )
    result = validate_integration_plan(_profiles_master_detail(), plan)
    assert result.lineage
    assert result.lineage[0]["op"] == "join"
