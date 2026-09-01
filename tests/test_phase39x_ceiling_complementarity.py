"""Phase 39X — observability classification is offline and architecture-safe."""

from __future__ import annotations

import inspect
from pathlib import Path

from core.integrate.semantic_escalation import SEMANTIC_VERIFIER_VARIANT
from tests.benchmark_multi.phase39v_research import evaluate_capability_signal
from tests.benchmark_multi.phase39x_research import META, PHASE39W_SHA, production_payload
from tests.benchmark_multi.phase39w_research import PHASE39V_RULE_VERSION, build_w_corpus


def test_39w_sha_and_frozen_rule() -> None:
    assert PHASE39W_SHA == "d25c87a36a4409035c8ca78e68938ad81a894373"
    assert PHASE39V_RULE_VERSION == "PHASE39V_RULE_V1"
    src = inspect.getsource(evaluate_capability_signal)
    assert "has_final_grain_contradiction" in src
    assert "C03" not in src


def test_production_verifier_still_omits_result() -> None:
    assert SEMANTIC_VERIFIER_VARIANT == "V1"
    esc = Path("core/integrate/semantic_escalation.py").read_text()
    assert "result=None" in esc
    assert "PHASE39X" not in esc


def test_corpus_reuses_39w_and_covers_roles() -> None:
    w_ids = {c["attempt_id"] for c in build_w_corpus()}
    assert set(META) <= w_ids
    roles = {m["role"] for m in META.values()}
    assert {"blind", "lookalike", "observable", "cannot_plan", "m2m"} <= roles
    assert sum(1 for m in META.values() if m["role"] == "blind") == 7
    assert sum(1 for m in META.values() if m["role"] == "lookalike") >= 7


def test_no_semantic_python_shortcuts_in_classifier() -> None:
    src = Path("tests/benchmark_multi/phase39x_research.py").read_text()
    assert "if prompt_requested_two_sides" not in src
    assert "PHASE39X_RULE" not in src
    # annotations are labels, not production routing
    assert "evaluate_capability_signal" in src


def test_production_payload_has_no_observed_result() -> None:
    from tests.benchmark_multi.phase39x_research import build_rows

    rows = build_rows()
    blind = next(r for r in rows if r["role"] == "blind")
    payload = production_payload(blind)
    assert "observed_result" not in payload
    assert "user_prompt" in payload
    assert "plan_structure" in payload
    assert "materialization_evidence" in payload
