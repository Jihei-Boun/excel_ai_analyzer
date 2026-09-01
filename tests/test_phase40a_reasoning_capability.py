"""Phase 40A — research harness only; production verifier frozen."""

from __future__ import annotations

from pathlib import Path

from core.integrate.semantic_escalation import (
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
)
from tests.benchmark_multi.phase40a_research import (
    ALL_IDS,
    DEV_IDS,
    HOLD_IDS,
    P1_ADDENDUM,
    P2_ADDENDUM,
    P3_ADDENDUM,
    P5_ADDENDUM,
    PHASE39Z_SHA,
    PROMPT_REGISTRY,
    VALID_IDS,
    WRONG_IDS,
    production_user_prefix,
)


def test_39z_sha_and_production_frozen() -> None:
    assert PHASE39Z_SHA == "9688e504c2784d9441e30d8f29173fa1f9422223"
    assert SEMANTIC_VERIFIER_VARIANT == "V1"
    assert SEMANTIC_VERIFIER_MODEL == "qwen2.5:7b"
    esc = Path("core/integrate/semantic_escalation.py").read_text()
    assert "observe_result_for_verifier" in esc
    assert "result=observed" in esc
    ver = Path("core/integrate/semantic_verifier.py").read_text()
    assert "PHASE40A" not in ver
    assert "PHASE40A" not in esc


def test_p0_prefix_matches_production_source() -> None:
    src = Path("core/integrate/semantic_verifier.py").read_text()
    p0 = production_user_prefix(result_attached=True)
    assert "and observed result" in p0
    assert "Step order (mandatory):" in src
    assert "Reconstruct material requirements from user_prompt only." in src
    assert "Optionally glance at planner_claims" in src


def test_new_prompt_addenda_are_generic() -> None:
    blob = P1_ADDENDUM + P2_ADDENDUM + P3_ADDENDUM + P5_ADDENDUM
    for banned in (
        "group_by",
        "check group",
        "agent vs",
        "transaction id",
        "this case should fail",
        "w2-wrong",
        "join/",
        "union should",
    ):
        assert banned not in blob.lower() or banned not in blob
    assert "group_by" not in blob
    assert "tid" not in blob
    assert PROMPT_REGISTRY["P0"]["sha256"]
    assert PROMPT_REGISTRY["P2"]["sha256"] != PROMPT_REGISTRY["P0"]["sha256"]


def test_corpus_shape_and_holdout() -> None:
    assert 20 <= len(ALL_IDS) <= 30
    assert len(VALID_IDS) >= 10
    assert "w2-wrong-group-grain" in DEV_IDS
    assert "a40-wrong-receipt-grain" in HOLD_IDS
    assert any(x in HOLD_IDS for x in ("w2-filter-wrong-site", "w2-single-run-only", "a40-wrong-desk-branch"))
    assert "w5-valid-multi-stage" in HOLD_IDS
    assert len(WRONG_IDS) >= 3
    assert set(DEV_IDS) & set(HOLD_IDS) == set()
