"""Phase 39Y — evidence ablation is harness-only; production verifier frozen."""

from __future__ import annotations

from pathlib import Path

from core.integrate.semantic_escalation import SEMANTIC_VERIFIER_VARIANT
from tests.benchmark_multi.phase39x_research import production_payload
from tests.benchmark_multi.phase39y_research import (
    LOOK_IDS,
    PHASE39X_SHA,
    WRONG_IDS,
    Y_IDS,
    _bound_result,
)


def test_39x_sha_frozen() -> None:
    assert PHASE39X_SHA == "decb584ab169aa659f0920c2a6ac514624d38a1f"


def test_corpus_includes_anchors_and_lookalikes() -> None:
    assert "w2-join-instead-of-union" in WRONG_IDS
    assert "w2-wrong-group-grain" in WRONG_IDS
    assert len(WRONG_IDS) >= 7
    assert len(LOOK_IDS) >= 8
    assert len(Y_IDS) >= 17


def test_production_still_v1_after_39z_observation() -> None:
    """39Y freeze: variant remains V1. 39Z added observation plumbing only."""
    assert SEMANTIC_VERIFIER_VARIANT == "V1"
    esc = Path("core/integrate/semantic_escalation.py").read_text()
    assert "PHASE39Y" not in esc
    assert "observe_result_for_verifier" in esc
    assert "result=observed" in esc


def test_result_observation_is_bounded_and_generic() -> None:
    obs = {
        "row_count": 9,
        "columns": [f"c{i}" for i in range(30)],
        "sample_rows": [{"a": i} for i in range(10)],
    }
    full = _bound_result(obs, mode="full")
    assert full is not None
    assert len(full["sample_rows"]) <= 5
    assert len(full["columns"]) <= 24
    assert "expected_answer" not in full


def test_v0_payload_omits_observed_result() -> None:
    from tests.benchmark_multi.phase39x_research import build_rows

    rows = build_rows()
    rec = next(r for r in rows if r["attempt_id"] == "w2-join-instead-of-union")
    payload = production_payload(rec)
    assert "observed_result" not in payload
    assert "user_prompt" in payload
