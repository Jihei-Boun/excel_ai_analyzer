"""Phase 38 evaluation unit tests — classification only, no semantic winner."""

from __future__ import annotations

from core.shadow.fingerprint import structural_compare
from tests.benchmark_multi.phase38_eval import (
    SOURCE_REAL,
    SOURCE_REPLAY,
    classify_failure_family,
    classify_path,
    matrix_cell,
    normalize_observation,
    production_gate,
)


def test_matrix_cells() -> None:
    assert matrix_cell(True, True) == "L+_S+"
    assert matrix_cell(True, False) == "L+_S-"
    assert matrix_cell(False, True) == "L-_S+"
    assert matrix_cell(False, False) == "L-_S-"


def test_structural_similar_vs_equal() -> None:
    a = {
        "shape": [2, 2],
        "columns": ["id", "x"],
        "content_hash_head50": "aaa",
    }
    b = {
        "shape": [2, 2],
        "columns": ["x", "id"],
        "content_hash_head50": "bbb",
    }
    assert structural_compare(a, a) == "structurally_equal"
    assert structural_compare(a, b) == "structurally_similar"


def test_path_classification() -> None:
    assert (
        classify_path(
            {
                "semantic_verifier_invoked": True,
                "semantic_verifier_verdict": "pass",
                "failure_32b_invoked": False,
                "semantic_32b_invoked": False,
            }
        )
        == "A_fast_verifier_pass"
    )
    assert (
        classify_path({"failure_32b_invoked": True, "semantic_32b_invoked": False})
        == "B_failure_strong"
    )
    assert (
        classify_path({"semantic_32b_invoked": True, "failure_32b_invoked": False})
        == "C_semantic_strong"
    )


def test_failure_infra_vs_model() -> None:
    assert (
        classify_failure_family({"error_family": "shadow_skipped_capacity"})
        == "shadow_skipped_capacity"
    )
    assert (
        classify_failure_family({"cannot_plan": True, "shadow_success": False})
        == "cannot_plan"
    )


def test_normalize_tags_source() -> None:
    rec = {
        "evidence_source": SOURCE_REPLAY,
        "legacy": {"legacy_success": True},
        "shadow": {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_success": True,
            "semantic_verifier_invoked": True,
            "semantic_verifier_verdict": "pass",
        },
    }
    o = normalize_observation(rec)
    assert o["evidence_source"] == SOURCE_REPLAY
    assert o["matrix"] == "L+_S+"


def test_replay_without_legacy_has_no_matrix() -> None:
    rec = {
        "evidence_source": SOURCE_REPLAY,
        "legacy": {"legacy_success": None, "legacy_available": False},
        "shadow": {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_success": True,
            "cannot_plan": False,
        },
    }
    o = normalize_observation(rec)
    assert o["matrix"] is None
    assert o["legacy_available"] is False


def test_gate_insufficient_without_real() -> None:
    gate = production_gate(
        real_agg={"n": 0, "unsafe_execution_count": 0, "shadow_completed": 0},
        replay_agg={"n": 57, "unsafe_execution_count": 0},
        real_n=0,
        manual_review={"reviewed_n": 0, "systematic_shadow_harm_count": 0},
        production_impact={"legacy_unaffected": True},
    )
    assert gate["recommendation"] == "C_evidence_insufficient"
    assert gate["gates"][-1]["id"] == "G12"
    assert gate["gates"][-1]["result"] == "NO"  # insufficient evidence → G12 NO
    g1 = next(g for g in gate["gates"] if g["id"] == "G1")
    assert g1["result"] == "YES"


def test_no_semantic_winner_in_compare() -> None:
    # structural_compare never returns better/worse
    r = structural_compare(
        {"shape": [1, 1], "columns": ["a"], "content_hash_head50": "1"},
        {"shape": [1, 1], "columns": ["a"], "content_hash_head50": "2"},
    )
    assert r in {"structurally_similar", "structurally_equal", "structurally_different"}
    assert "better" not in r
