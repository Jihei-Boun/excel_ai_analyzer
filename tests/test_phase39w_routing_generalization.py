"""Phase 39W — frozen 39V rule stays unmodified; research harness is offline."""

from __future__ import annotations

import inspect
from pathlib import Path

from tests.benchmark_multi.phase39v_research import evaluate_capability_signal
from tests.benchmark_multi.phase39w_research import (
    PHASE39V_RULE_EXPR,
    PHASE39V_RULE_VERSION,
    PHASE39V_SHA,
    build_w_corpus,
    phase39v_rule_v1,
)


def test_wrapper_is_unmodified_import() -> None:
    src = inspect.getsource(phase39v_rule_v1)
    assert "evaluate_capability_signal(ev)" in src
    assert PHASE39V_RULE_VERSION == "PHASE39V_RULE_V1"
    assert "final_grain_contradiction" in PHASE39V_RULE_EXPR
    assert PHASE39V_SHA == "fe8b5994e7ce18406c10c599a8c661508a27bd0e"


def test_frozen_rule_source_has_no_leakage_tokens() -> None:
    src = inspect.getsource(evaluate_capability_signal)
    for token in (
        "benchmark",
        "C03",
        "D01",
        "D02",
        "phase39",
        "union_rows",
        "n_sources",
        "filename",
    ):
        assert token not in src, token
    assert "category or domain" in src  # docstring forbids domain features


def test_new_corpus_is_not_39v_clone() -> None:
    corpus = build_w_corpus()
    ids = [c["attempt_id"] for c in corpus]
    assert len(ids) >= 36
    assert len(ids) == len(set(ids))
    assert all(i.startswith("w") for i in ids)
    assert not any(i.startswith(("g1-", "g2-", "g3-", "c03", "d01", "d02")) for i in ids)
    groups = {c["group"] for c in corpus}
    assert {"W1", "W2", "W3", "W4", "W5", "W6"} <= groups


def test_rule_does_not_read_manual_label() -> None:
    ev = {
        "planner_declared_cannot_plan": False,
        "has_final_grain_contradiction": False,
        "evidence_role_contradiction": False,
        "has_structural_error": False,
        "only_unsafe_codes": False,
        "fast_correct": "NO",
        "benchmark_id": "C03",
    }
    assert phase39v_rule_v1(ev) == "DO_NOT_ESCALATE"
    ev["has_final_grain_contradiction"] = True
    assert phase39v_rule_v1(ev) == "ESCALATE"


def test_cannot_plan_never_escalated_by_frozen_rule() -> None:
    ev = {
        "planner_declared_cannot_plan": True,
        "has_final_grain_contradiction": True,
        "evidence_role_contradiction": True,
        "has_structural_error": True,
        "only_unsafe_codes": False,
    }
    assert phase39v_rule_v1(ev) == "DO_NOT_ESCALATE"


def test_research_files_do_not_touch_production_routing() -> None:
    w_src = Path("tests/benchmark_multi/phase39w_research.py").read_text()
    assert "should_escalate_after_fast_path" not in w_src or "simulate" in w_src
    assert "PlannerModelStrategy(" not in w_src
    prod = Path("core/integrate/planner_model_strategy.py").read_text()
    assert "PHASE39W" not in prod
    assert "phase39v_rule_v1" not in prod
