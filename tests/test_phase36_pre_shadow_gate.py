"""Phase 36 unit tests — path classification & frozen architecture invariants."""

from __future__ import annotations

from core.integrate.semantic_escalation import (
    MAX_SEMANTIC_ESCALATIONS,
    SEMANTIC_VERIFIER_VARIANT,
)
from tests.benchmark_multi.phase36_pre_shadow_gate import (
    classify_path,
    analyze_verifier_reachability,
)


def test_path_a_fast_pass() -> None:
    c = {
        "metadata": {
            "failure_escalation_32b": False,
            "semantic_escalation_32b": False,
            "semantic_verifier_invoked": True,
            "semantic_verifier": {"verdict": "pass"},
        }
    }
    assert classify_path(c) == "A_fast_verifier_pass"


def test_path_b_failure_strong() -> None:
    c = {
        "metadata": {
            "failure_escalation_32b": True,
            "semantic_escalation_32b": False,
            "semantic_verifier_invoked": True,
            "semantic_verifier": {"verdict": "pass"},
        }
    }
    assert classify_path(c) == "B_failure_strong"


def test_path_c_semantic_strong() -> None:
    c = {
        "metadata": {
            "failure_escalation_32b": False,
            "semantic_escalation_32b": True,
            "semantic_verifier_invoked": True,
            "semantic_verifier": {"verdict": "fail"},
        }
    }
    assert classify_path(c) == "C_semantic_strong"


def test_path_d_double_strong_attribution() -> None:
    c = {
        "metadata": {
            "failure_escalation_32b": True,
            "semantic_escalation_32b": True,
            "semantic_verifier_invoked": True,
            "semantic_verifier": {"verdict": "fail"},
        }
    }
    assert classify_path(c) == "D_double_strong"


def test_path_d_not_reached() -> None:
    c = {
        "status": "cannot_plan",
        "metadata": {
            "failure_escalation_32b": False,
            "semantic_escalation_32b": False,
            "semantic_verifier_invoked": False,
        },
    }
    assert classify_path(c) == "D_verifier_not_reached"


def test_verifier_eligibility_accounting() -> None:
    cases = [
        {"status": "success", "metadata": {"semantic_verifier_invoked": True}},
        {"status": "cannot_plan", "metadata": {"semantic_verifier_invoked": False}},
        {
            "status": "failed",
            "failure_categories": ["retry_exhausted"],
            "metadata": {"semantic_verifier_invoked": False},
        },
    ]
    reach = analyze_verifier_reachability(cases)
    assert reach["equality_check"]["ok"] is True
    assert reach["verifier_invoked"] == 1
    assert reach["verifier_not_reached"] == 2
    assert reach["not_reached_taxonomy"]["cannot_plan"] == 1
    assert reach["not_reached_taxonomy"]["retry_exhausted"] == 1


def test_frozen_semantic_budget_and_variant() -> None:
    assert MAX_SEMANTIC_ESCALATIONS == 1
    assert SEMANTIC_VERIFIER_VARIANT == "V1"


def test_no_production_wiring() -> None:
    from pathlib import Path

    rm = Path("core/routing/route_multi.py").read_text(encoding="utf-8")
    assert "semantic_escalation" not in rm
    assert "run_integration_pipeline_semantic" not in rm
    src = Path("core/integrate/semantic_escalation.py").read_text(encoding="utf-8")
    assert "MAX_SEMANTIC_ESCALATIONS = 1" in src
    assert "reverify_strong: bool = False" in src
