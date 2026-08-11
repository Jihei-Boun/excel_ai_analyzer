"""Phase 14: Cross-file Data Understanding tests."""

from __future__ import annotations

import pandas as pd
import pytest

from core.integrate.relationship_infer import (
    build_cross_file_understanding,
    infer_cross_file_relationship,
)
from core.integrate.relationship_profile import (
    _overlap_token,
    build_file_profile,
    build_pairwise_observation,
)
from core.integrate.relationship_types import RELATIONSHIP_VOCABULARY


def test_file_profile_is_observation_only() -> None:
    df = pd.DataFrame(
        {
            "customer_id": ["C1", "C2", "C3"],
            "amount": [10, 20, 30],
        }
    )
    profile = build_file_profile("sales_jan", df)
    assert profile.source_id == "sales_jan"
    assert profile.row_count == 3
    assert profile.column_count == 2
    cols = profile.observations["columns"]
    names = {c["name"] for c in cols}
    assert names == {"customer_id", "amount"}
    cust = next(c for c in cols if c["name"] == "customer_id")
    assert cust["uniqueness_ratio"] == 1.0
    assert cust["null_ratio"] == 0.0
    # Must not invent PK / additive truths
    assert "primary_key" not in profile.observations
    assert "additive_columns" not in profile.observations
    assert profile.semantic_hints == {}


def test_same_schema_pairwise_observation() -> None:
    jan = pd.DataFrame({"product": ["A", "B"], "qty": [1, 2], "revenue": [10, 20]})
    feb = pd.DataFrame({"product": ["A", "C"], "qty": [3, 4], "revenue": [30, 40]})
    obs = build_pairwise_observation("sales_jan", jan, "sales_feb", feb)
    assert obs.schema_similarity == 1.0
    assert set(obs.exact_column_name_overlap) == {"product", "qty", "revenue"}
    # No relationship / operation field on observation
    d = obs.to_dict()
    assert "relationship" not in d
    assert "recommended_operation" not in d


def test_master_detail_like_cardinality_evidence() -> None:
    customers = pd.DataFrame(
        {
            "customer_id": ["C1", "C2", "C3"],
            "name": ["Ann", "Bob", "Cara"],
        }
    )
    orders = pd.DataFrame(
        {
            "order_id": ["O1", "O2", "O3", "O4"],
            "customer_id": ["C1", "C1", "C2", "C3"],
            "amount": [10, 11, 20, 30],
        }
    )
    obs = build_pairwise_observation("customers", customers, "orders", orders)
    pair = next(
        p
        for p in obs.candidate_pairs
        if p.left_column == "customer_id" and p.right_column == "customer_id"
    )
    assert pair.value_overlap_ratio >= 0.9
    assert pair.left_uniqueness == 1.0
    assert pair.right_uniqueness < 1.0
    assert pair.cardinality_evidence == "one_to_many"
    # Observation must not claim join decision
    assert not hasattr(pair, "recommended_operation")


def test_lookup_like_products_sales() -> None:
    products = pd.DataFrame(
        {
            "product_id": ["P1", "P2", "P3"],
            "category": ["x", "y", "z"],
        }
    )
    sales = pd.DataFrame(
        {
            "sale_id": [1, 2, 3, 4],
            "product_id": ["P1", "P1", "P2", "P3"],
            "units": [1, 2, 1, 5],
        }
    )
    obs = build_pairwise_observation("products", products, "sales", sales)
    pair = next(
        p
        for p in obs.candidate_pairs
        if p.left_column == "product_id" and p.right_column == "product_id"
    )
    assert pair.dtype_compatible
    assert pair.value_overlap_ratio >= 0.9
    assert pair.cardinality_evidence in {"one_to_many", "one_to_one"}


def test_unrelated_files_low_schema_overlap() -> None:
    employees = pd.DataFrame(
        {
            "employee_id": ["E1", "E2"],
            "department": ["HR", "Eng"],
            "salary": [100, 200],
        }
    )
    sensor = pd.DataFrame(
        {
            "device_id": ["D1", "D2", "D3"],
            "timestamp": ["t1", "t2", "t3"],
            "temperature": [21.5, 22.0, 19.0],
        }
    )
    obs = build_pairwise_observation("employees", employees, "sensor", sensor)
    assert obs.schema_similarity == 0.0
    assert obs.exact_column_name_overlap == []
    # May still have weak candidates by dtype+name noise; overlap should be low
    for pair in obs.candidate_pairs:
        assert pair.value_overlap_ratio < 0.5 or pair.name_similarity < 0.5


def test_same_column_name_different_values_not_forced() -> None:
    sales = pd.DataFrame({"id": ["S1", "S2", "S3"], "amount": [1, 2, 3]})
    survey = pd.DataFrame({"id": [100, 200, 300], "score": [4, 5, 3]})
    obs = build_pairwise_observation("sales", sales, "survey", survey)
    id_pair = next(
        (p for p in obs.candidate_pairs if p.left_column == "id" and p.right_column == "id"),
        None,
    )
    assert id_pair is not None  # name match keeps candidate for LLM to judge
    assert id_pair.name_exact_match is True
    assert id_pair.value_overlap_ratio == 0.0  # must not invent overlap


def test_ambiguous_key_candidates_preserved_in_observation() -> None:
    left = pd.DataFrame(
        {
            "customer_id": ["C1", "C2"],
            "customer_code": ["X1", "X2"],
            "account_id": ["A1", "A2"],
        }
    )
    right = pd.DataFrame(
        {
            "customer_id": ["C1", "C2", "C1"],
            "customer_code": ["X1", "X2", "X1"],
            "account_id": ["A1", "A2", "A1"],
            "amt": [1, 2, 3],
        }
    )
    obs = build_pairwise_observation("left", left, "right", right)
    keys = {(p.left_column, p.right_column) for p in obs.candidate_pairs}
    # Multiple strong pairs should remain candidates (ambiguity for LLM)
    assert ("customer_id", "customer_id") in keys
    assert ("customer_code", "customer_code") in keys
    assert ("account_id", "account_id") in keys


def test_dirty_value_overlap_normalization() -> None:
    assert _overlap_token("001") == _overlap_token(1) == _overlap_token(" 001 ")
    left = pd.DataFrame({"code": ["001", "002", "003"]})
    right = pd.DataFrame({"code": [1, 2, 3, 1]})
    obs = build_pairwise_observation("a", left, "b", right)
    pair = next(p for p in obs.candidate_pairs if p.left_column == "code")
    assert pair.value_overlap_ratio == 1.0
    assert pair.cardinality_evidence == "one_to_many"


def test_llm_relationship_same_schema_mock() -> None:
    jan = pd.DataFrame({"product": ["A"], "qty": [1]})
    feb = pd.DataFrame({"product": ["B"], "qty": [2]})
    obs = build_pairwise_observation("sales_jan", jan, "sales_feb", feb)

    def chat_json(_prompt: str, **_kwargs):
        return {
            "relationship": "same_schema",
            "key_candidates": [],
            "confidence": 0.9,
            "evidence": ["schema_similarity=1.0", "exact column overlap"],
            "ambiguities": [],
            "notes": [],
        }

    rel = infer_cross_file_relationship(obs, chat_json_fn=chat_json)
    assert rel.relationship == "same_schema"
    assert "operation" not in rel.to_dict()
    assert "recommended_operation" not in rel.to_dict()


def test_llm_relationship_unrelated_mock() -> None:
    emp = pd.DataFrame({"employee_id": ["E1"], "dept": ["HR"]})
    sensor = pd.DataFrame({"device_id": ["D1"], "temp": [20.0]})
    obs = build_pairwise_observation("employees", emp, "sensor", sensor)

    def chat_json(_prompt: str, **_kwargs):
        return {
            "relationship": "unrelated",
            "key_candidates": [],
            "confidence": 0.85,
            "evidence": ["schema_similarity=0", "no overlapping values"],
            "ambiguities": [],
            "notes": [],
        }

    rel = infer_cross_file_relationship(obs, chat_json_fn=chat_json)
    assert rel.relationship == "unrelated"
    assert rel.key_candidates == []


def test_llm_relationship_ambiguous_mock() -> None:
    left = pd.DataFrame({"customer_id": ["C1"], "customer_code": ["X1"]})
    right = pd.DataFrame(
        {"customer_id": ["C1", "C1"], "customer_code": ["X1", "X1"], "v": [1, 2]}
    )
    obs = build_pairwise_observation("l", left, "r", right)

    def chat_json(_prompt: str, **_kwargs):
        return {
            "relationship": "ambiguous",
            "key_candidates": [
                {"left_column": "customer_id", "right_column": "customer_id", "confidence": 0.7},
                {
                    "left_column": "customer_code",
                    "right_column": "customer_code",
                    "confidence": 0.68,
                },
            ],
            "confidence": 0.4,
            "evidence": ["multiple similar key pairs"],
            "ambiguities": ["customer_id vs customer_code"],
            "notes": [],
        }

    rel = infer_cross_file_relationship(obs, chat_json_fn=chat_json)
    assert rel.relationship == "ambiguous"
    assert len(rel.key_candidates) == 2
    assert rel.ambiguities


def test_llm_rejects_invalid_vocab_as_insufficient() -> None:
    df_a = pd.DataFrame({"a": [1]})
    df_b = pd.DataFrame({"b": [2]})
    obs = build_pairwise_observation("a", df_a, "b", df_b)

    def chat_json(_prompt: str, **_kwargs):
        return {"relationship": "must_union_now", "key_candidates": []}

    rel = infer_cross_file_relationship(obs, chat_json_fn=chat_json, max_parse_retries=0)
    assert rel.relationship == "insufficient_evidence"
    assert "llm_parse_or_call_failed" in rel.ambiguities


def test_build_cross_file_understanding_package() -> None:
    frames = [
        ("customers", pd.DataFrame({"customer_id": ["C1", "C2"], "name": ["A", "B"]})),
        (
            "orders",
            pd.DataFrame(
                {
                    "order_id": [1, 2, 3],
                    "customer_id": ["C1", "C1", "C2"],
                    "amount": [10, 11, 20],
                }
            ),
        ),
    ]

    def chat_json(_prompt: str, **_kwargs):
        return {
            "relationship": "master_detail_candidate",
            "key_candidates": [
                {
                    "left_column": "customer_id",
                    "right_column": "customer_id",
                    "confidence": 0.9,
                    "why": "high overlap; left unique right repeated",
                }
            ],
            "confidence": 0.88,
            "evidence": ["cardinality_evidence=one_to_many"],
            "ambiguities": [],
            "notes": [],
            # Must be ignored — Phase 14 forbids ops
            "recommended_operation": "join",
        }

    result = build_cross_file_understanding(
        frames,
        chat_json_fn=chat_json,
    )
    assert len(result.file_profiles) == 2
    assert len(result.pairwise_observations) == 1
    assert len(result.relationships) == 1
    assert result.relationships[0].relationship == "master_detail_candidate"
    assert result.meta.get("integration_operations") is None
    payload = result.to_dict()
    assert "file_profiles" in payload
    assert "relationships" in payload


def test_relationship_vocabulary_covers_safe_failure() -> None:
    for name in ("unrelated", "ambiguous", "insufficient_evidence"):
        assert name in RELATIONSHIP_VOCABULARY


def test_observations_only_mode_skips_llm() -> None:
    frames = [
        ("a", pd.DataFrame({"x": [1, 2]})),
        ("b", pd.DataFrame({"x": [1, 3]})),
    ]
    result = build_cross_file_understanding(frames, infer_relationships=False)
    assert result.relationships == []
    assert len(result.pairwise_observations) == 1
