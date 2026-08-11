"""Phase 15: IntegrationPlan v1 & LLM Integration Planner tests."""

from __future__ import annotations

import pytest

from core.integrate.integration_plan_types import (
    INTEGRATION_ATOMIC_OPS,
    IntegrationPlanParseError,
    canonical_integration_plan_signature,
    integration_plan_from_dict,
)
from core.integrate.integration_planner import build_integration_plan
from core.integrate.relationship_types import CrossFileUnderstanding


def _understanding_same_schema() -> dict:
    return {
        "file_profiles": [
            {
                "source_id": "sales_jan",
                "row_count": 2,
                "column_count": 2,
                "observations": {
                    "columns": [
                        {
                            "name": "product",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": ["A", "B"],
                        },
                        {
                            "name": "qty",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [1, 2],
                        },
                    ],
                    "column_names": ["product", "qty"],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "sales_feb",
                "row_count": 2,
                "column_count": 2,
                "observations": {
                    "columns": [
                        {
                            "name": "product",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": ["A", "C"],
                        },
                        {
                            "name": "qty",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [3, 4],
                        },
                    ],
                    "column_names": ["product", "qty"],
                },
                "semantic_hints": {},
            },
        ],
        "pairwise_observations": [
            {
                "left_source": "sales_feb",
                "right_source": "sales_jan",
                "schema_similarity": 1.0,
                "exact_column_name_overlap": ["product", "qty"],
                "candidate_pairs": [],
            }
        ],
        "relationships": [
            {
                "left_source": "sales_feb",
                "right_source": "sales_jan",
                "relationship": "same_schema",
                "key_candidates": [],
                "confidence": 0.9,
                "evidence": ["schema_similarity=1.0"],
                "ambiguities": [],
            }
        ],
    }


def _understanding_master_detail() -> dict:
    return {
        "file_profiles": [
            {
                "source_id": "customers",
                "row_count": 3,
                "column_count": 2,
                "observations": {
                    "columns": [
                        {
                            "name": "customer_id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1.0,
                            "distinct_count": 3,
                            "sample_values": ["C1", "C2"],
                        },
                        {
                            "name": "name",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1.0,
                            "distinct_count": 3,
                            "sample_values": ["A", "B"],
                        },
                    ],
                    "column_names": ["customer_id", "name"],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "orders",
                "row_count": 4,
                "column_count": 3,
                "observations": {
                    "columns": [
                        {
                            "name": "order_id",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1.0,
                            "distinct_count": 4,
                            "sample_values": [1, 2],
                        },
                        {
                            "name": "customer_id",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 0.75,
                            "distinct_count": 3,
                            "sample_values": ["C1", "C2"],
                        },
                        {
                            "name": "amount",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1.0,
                            "distinct_count": 4,
                            "sample_values": [10, 11],
                        },
                    ],
                    "column_names": ["order_id", "customer_id", "amount"],
                },
                "semantic_hints": {},
            },
        ],
        "pairwise_observations": [
            {
                "left_source": "customers",
                "right_source": "orders",
                "schema_similarity": 0.25,
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
                        "confidence": 0.91,
                    }
                ],
                "confidence": 0.88,
                "evidence": ["one_to_many"],
                "ambiguities": [],
            }
        ],
    }


def test_atomic_ops_exclude_aggregate_merge() -> None:
    assert "aggregate_merge" not in INTEGRATION_ATOMIC_OPS
    assert {"rename_columns", "filter_rows", "union_rows", "join", "aggregate", "select_columns"} <= INTEGRATION_ATOMIC_OPS


def test_parse_planned_union() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "id": "step_1",
                    "op": "union_rows",
                    "inputs": ["sales_jan", "sales_feb"],
                    "output": "sales_all",
                    "params": {},
                }
            ],
            "final_output": "sales_all",
            "notes": [],
        }
    )
    assert plan.status == "planned"
    assert plan.steps[0].op == "union_rows"
    assert plan.steps[0].params["column_policy"] == "aligned"


def test_parse_cannot_plan() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "cannot_plan",
            "steps": [],
            "final_output": None,
            "reason": "unrelated files",
            "ambiguities": ["no shared keys"],
        }
    )
    assert plan.status == "cannot_plan"
    assert plan.steps == []
    assert plan.final_output is None


def test_unsupported_op_not_rewritten() -> None:
    with pytest.raises(IntegrationPlanParseError, match="unsupported op"):
        integration_plan_from_dict(
            {
                "status": "planned",
                "steps": [
                    {
                        "op": "pivot_magic",
                        "inputs": ["a"],
                        "output": "b",
                        "params": {},
                    }
                ],
                "final_output": "b",
            }
        )


def test_missing_join_keys_structural_reject() -> None:
    with pytest.raises(IntegrationPlanParseError, match="left_keys"):
        integration_plan_from_dict(
            {
                "status": "planned",
                "steps": [
                    {
                        "op": "join",
                        "inputs": ["customers", "orders"],
                        "output": "joined",
                        "params": {"how": "left"},
                    }
                ],
                "final_output": "joined",
            }
        )


def test_missing_aggregate_metrics_not_autofilled() -> None:
    with pytest.raises(IntegrationPlanParseError, match="metrics"):
        integration_plan_from_dict(
            {
                "status": "planned",
                "steps": [
                    {
                        "op": "aggregate",
                        "inputs": ["combined"],
                        "output": "summary",
                        "params": {"group_by": ["product"]},
                    }
                ],
                "final_output": "summary",
            }
        )


def test_planner_same_schema_union_mock() -> None:
    def chat_json(_prompt: str, **_kwargs):
        return {
            "status": "planned",
            "steps": [
                {
                    "id": "step_1",
                    "op": "union_rows",
                    "inputs": ["sales_jan", "sales_feb"],
                    "output": "sales_all",
                    "params": {"column_policy": "aligned"},
                }
            ],
            "final_output": "sales_all",
            "notes": [],
            "ambiguities": [],
        }

    plan = build_integration_plan(
        "두 달 판매 데이터를 하나로 통합해줘",
        _understanding_same_schema(),
        chat_json_fn=chat_json,
    )
    assert plan.status == "planned"
    assert [s.op for s in plan.steps] == ["union_rows"]
    assert "aggregate_merge" not in plan.to_dict()["steps"][0]["op"]


def test_planner_master_detail_join_mock() -> None:
    def chat_json(_prompt: str, **_kwargs):
        return {
            "status": "planned",
            "steps": [
                {
                    "id": "step_1",
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
                    "id": "step_2",
                    "op": "select_columns",
                    "inputs": ["joined"],
                    "output": "final",
                    "params": {
                        "columns": ["customer_id", "name", "order_id", "amount"]
                    },
                },
            ],
            "final_output": "final",
            "notes": [],
            "ambiguities": [],
        }

    plan = build_integration_plan(
        "고객 정보와 주문 정보를 연결해줘",
        _understanding_master_detail(),
        chat_json_fn=chat_json,
    )
    assert plan.status == "planned"
    assert [s.op for s in plan.steps] == ["join", "select_columns"]
    assert plan.steps[0].params["left_keys"] == ["customer_id"]


def test_planner_union_then_aggregate_mock() -> None:
    understanding = {
        "file_profiles": [
            {
                "source_id": "inventory_a",
                "row_count": 2,
                "column_count": 2,
                "observations": {
                    "columns": [
                        {
                            "name": "product",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": ["P1"],
                        },
                        {
                            "name": "qty",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [1],
                        },
                    ],
                    "column_names": ["product", "qty"],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "inventory_b",
                "row_count": 2,
                "column_count": 2,
                "observations": {
                    "columns": [
                        {
                            "name": "product",
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": ["P1"],
                        },
                        {
                            "name": "qty",
                            "dtype_family": "numeric",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 2,
                            "sample_values": [5],
                        },
                    ],
                    "column_names": ["product", "qty"],
                },
                "semantic_hints": {},
            },
        ],
        "pairwise_observations": [
            {
                "left_source": "inventory_a",
                "right_source": "inventory_b",
                "schema_similarity": 1.0,
                "exact_column_name_overlap": ["product", "qty"],
                "candidate_pairs": [],
            }
        ],
        "relationships": [
            {
                "left_source": "inventory_a",
                "right_source": "inventory_b",
                "relationship": "same_schema",
                "key_candidates": [],
                "confidence": 0.9,
                "evidence": [],
                "ambiguities": [],
            }
        ],
    }

    def chat_json(_prompt: str, **_kwargs):
        return {
            "status": "planned",
            "steps": [
                {
                    "op": "union_rows",
                    "inputs": ["inventory_a", "inventory_b"],
                    "output": "combined",
                    "params": {},
                },
                {
                    "op": "aggregate",
                    "inputs": ["combined"],
                    "output": "summary",
                    "params": {
                        "group_by": ["product"],
                        "metrics": [{"column": "qty", "function": "sum"}],
                    },
                },
            ],
            "final_output": "summary",
            "notes": [],
            "ambiguities": [],
        }

    plan = build_integration_plan(
        "두 파일의 재고를 제품별로 통합해서 수량을 합쳐줘",
        understanding,
        chat_json_fn=chat_json,
    )
    assert [s.op for s in plan.steps] == ["union_rows", "aggregate"]
    assert plan.steps[1].params["metrics"][0]["function"] == "sum"


def test_planner_ambiguous_cannot_plan_mock() -> None:
    understanding = {
        "file_profiles": [
            {"source_id": "a", "row_count": 1, "column_count": 2, "observations": {"columns": [], "column_names": ["customer_id", "account_id"]}, "semantic_hints": {}},
            {"source_id": "b", "row_count": 1, "column_count": 2, "observations": {"columns": [], "column_names": ["customer_id", "account_id"]}, "semantic_hints": {}},
        ],
        "pairwise_observations": [],
        "relationships": [
            {
                "left_source": "a",
                "right_source": "b",
                "relationship": "ambiguous",
                "key_candidates": [
                    {"left_column": "customer_id", "right_column": "customer_id", "confidence": 0.7},
                    {"left_column": "account_id", "right_column": "account_id", "confidence": 0.68},
                ],
                "confidence": 0.4,
                "evidence": [],
                "ambiguities": ["customer_id vs account_id"],
            }
        ],
    }

    def chat_json(_prompt: str, **_kwargs):
        return {
            "status": "cannot_plan",
            "steps": [],
            "final_output": None,
            "reason": "Multiple plausible key relationships remain unresolved.",
            "ambiguities": ["customer_id and account_id both have strong evidence"],
            "notes": [],
        }

    plan = build_integration_plan("두 파일을 합쳐줘", understanding, chat_json_fn=chat_json)
    assert plan.status == "cannot_plan"
    assert plan.steps == []
    assert plan.ambiguities


def test_planner_unrelated_cannot_plan_mock() -> None:
    understanding = {
        "file_profiles": [
            {"source_id": "employees", "row_count": 1, "column_count": 1, "observations": {"columns": [], "column_names": ["employee_id"]}, "semantic_hints": {}},
            {"source_id": "sensor", "row_count": 1, "column_count": 1, "observations": {"columns": [], "column_names": ["device_id"]}, "semantic_hints": {}},
        ],
        "pairwise_observations": [
            {
                "left_source": "employees",
                "right_source": "sensor",
                "schema_similarity": 0.0,
                "exact_column_name_overlap": [],
                "candidate_pairs": [],
            }
        ],
        "relationships": [
            {
                "left_source": "employees",
                "right_source": "sensor",
                "relationship": "unrelated",
                "key_candidates": [],
                "confidence": 0.9,
                "evidence": ["schema_similarity=0"],
                "ambiguities": [],
            }
        ],
    }

    def chat_json(_prompt: str, **_kwargs):
        return {
            "status": "cannot_plan",
            "steps": [],
            "final_output": None,
            "reason": "No meaningful integration relationship for the request.",
            "ambiguities": [],
            "notes": [],
        }

    plan = build_integration_plan("두 파일을 합쳐줘", understanding, chat_json_fn=chat_json)
    assert plan.status == "cannot_plan"


def test_invalid_json_then_retry_success() -> None:
    calls = {"n": 0}

    def chat_json(_prompt: str, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": "planned", "steps": [{"op": "pivot_magic", "inputs": ["a"], "output": "b", "params": {}}]}
        return {
            "status": "cannot_plan",
            "steps": [],
            "final_output": None,
            "reason": "unsupported transform",
            "ambiguities": [],
            "notes": [],
        }

    plan = build_integration_plan(
        "피벗해줘",
        _understanding_same_schema(),
        chat_json_fn=chat_json,
        max_parse_retries=1,
    )
    assert calls["n"] == 2
    assert plan.status == "cannot_plan"
    assert plan.meta.get("parse_attempts") == 2


def test_parse_failure_exhausted_returns_cannot_plan() -> None:
    def chat_json(_prompt: str, **_kwargs):
        return {"status": "planned", "steps": [{"op": "nope", "inputs": ["a"], "output": "b", "params": {}}]}

    plan = build_integration_plan(
        "x",
        _understanding_same_schema(),
        chat_json_fn=chat_json,
        max_parse_retries=0,
    )
    assert plan.status == "cannot_plan"
    assert plan.reason == "planner_parse_failed"


def test_no_python_relationship_to_op_mapping() -> None:
    """Planner without LLM must not invent union from same_schema."""
    # Calling parser only — relationship label unused
    with pytest.raises(IntegrationPlanParseError):
        integration_plan_from_dict({"status": "planned", "steps": [], "final_output": None})


def test_canonical_signature_stable() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "union_rows",
                    "inputs": ["a", "b"],
                    "output": "u",
                    "params": {},
                }
            ],
            "final_output": "u",
        }
    )
    s1 = canonical_integration_plan_signature(plan)
    s2 = canonical_integration_plan_signature(plan.to_dict())
    assert s1 == s2
    assert "union_rows" in s1


def test_filter_operator_alias_normalize() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "steps": [
                {
                    "op": "filter_rows",
                    "inputs": ["file_a"],
                    "output": "detail",
                    "params": {
                        "conditions": [
                            {"column": "row_type", "operator": "==", "value": "detail"}
                        ]
                    },
                }
            ],
            "final_output": "detail",
        }
    )
    assert plan.steps[0].params["conditions"][0]["operator"] == "eq"


def test_build_with_cross_file_understanding_object() -> None:
    und = CrossFileUnderstanding(
        file_profiles=[],
        pairwise_observations=[],
        relationships=[],
        meta={},
    )

    def chat_json(_prompt: str, **_kwargs):
        return {
            "status": "cannot_plan",
            "steps": [],
            "final_output": None,
            "reason": "insufficient_evidence",
            "ambiguities": [],
            "notes": [],
        }

    plan = build_integration_plan("합쳐줘", und, chat_json_fn=chat_json)
    assert plan.status == "cannot_plan"
