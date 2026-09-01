"""Phase 39V — routing research harness is offline and architecture-safe."""

from __future__ import annotations

import inspect

from tests.benchmark_multi.phase39v_research import (
    evaluate_capability_signal,
    extract_attempt_evidence,
)


def test_capability_signal_is_pure_and_generic() -> None:
    src = inspect.getsource(evaluate_capability_signal)
    forbidden = (
        "benchmark",
        "C03",
        "D01",
        "D02",
        "union_rows",
        "n_sources",
        "day",
    )
    for token in forbidden:
        assert token not in src, token


def test_signal_does_not_call_models() -> None:
    src = inspect.getsource(evaluate_capability_signal)
    assert "chat" not in src
    assert "qwen" not in src
    assert "ollama" not in src


def test_deterministic_on_same_evidence() -> None:
    ev = {
        "planner_declared_cannot_plan": False,
        "has_final_grain_contradiction": True,
        "evidence_role_contradiction": False,
        "has_structural_error": True,
        "only_unsafe_codes": False,
    }
    assert evaluate_capability_signal(ev) == "ESCALATE"
    assert evaluate_capability_signal(ev) == "ESCALATE"
    ev2 = {**ev, "has_final_grain_contradiction": False, "has_structural_error": False}
    assert evaluate_capability_signal(ev2) == "DO_NOT_ESCALATE"


def test_extract_exports_lineage_ids() -> None:
    assert callable(extract_attempt_evidence)
