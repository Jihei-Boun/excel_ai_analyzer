"""Phase 35 unit tests — experimental semantic escalation (no production wiring)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from core.integrate.integration_pipeline import IntegrationPipelineResult
from core.integrate.integration_plan_types import (
    FinalOutputRequirements,
    IntegrationPlan,
    IntegrationStep,
)
from core.integrate.semantic_escalation import (
    MAX_SEMANTIC_ESCALATIONS,
    SEMANTIC_VERIFIER_VARIANT,
    SemanticEscalationConfig,
    build_semantic_replan_feedback,
    run_integration_pipeline_semantic_experimental,
)
from core.integrate.semantic_verifier import SemanticVerificationResult


def _plan(grain: str = "group") -> IntegrationPlan:
    return IntegrationPlan(
        status="planned",
        steps=[
            IntegrationStep(
                id="u",
                op="union_rows",
                inputs=["a", "b"],
                output="u",
                params={"column_policy": "aligned"},
            ),
            IntegrationStep(
                id="agg",
                op="aggregate",
                inputs=["u"],
                output="out",
                params={
                    "group_by": ["id"],
                    "metrics": [{"column": "x", "function": "sum", "alias": "sx"}],
                },
            ),
        ],
        final_output="out",
        final_output_requirements=FinalOutputRequirements(
            grain=grain, required_columns=["id", "sx"]
        ),
    )


def test_pass_does_not_semantic_escalate(monkeypatch) -> None:
    plan = _plan("detail")
    base = IntegrationPipelineResult(status="success", plan=plan, metadata={})

    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_integration_pipeline",
        lambda *a, **k: base,
    )

    def fake_verify(**kwargs):  # noqa: ANN003
        return SemanticVerificationResult(
            verdict="pass", reason_code="satisfied", evidence=["ok"], parse_ok=True
        )

    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_semantic_verification", fake_verify
    )
    strong_called = {"n": 0}

    def fake_loop(*a, **k):  # noqa: ANN003
        strong_called["n"] += 1
        return IntegrationPipelineResult(status="success", plan=plan, metadata={})

    monkeypatch.setattr(
        "core.integrate.semantic_escalation._run_integration_attempt_loop", fake_loop
    )

    out = run_integration_pipeline_semantic_experimental(
        "stack rows",
        {"a": pd.DataFrame({"id": [1]}), "b": pd.DataFrame({"id": [2]})},
        {"file_profiles": [], "relationships": []},
        config=SemanticEscalationConfig(enable_failure_escalation=False),
    )
    assert strong_called["n"] == 0
    assert out.metadata.get("semantic_escalation_32b") is False
    assert out.metadata.get("semantic_verifier_invoked") is True


def test_fail_triggers_one_semantic_escalation(monkeypatch) -> None:
    plan = _plan("group")
    base = IntegrationPipelineResult(status="success", plan=plan, metadata={})
    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_integration_pipeline",
        lambda *a, **k: base,
    )
    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_semantic_verification",
        lambda **k: SemanticVerificationResult(
            verdict="fail",
            reason_code="wrong_output_grain",
            evidence=["grain mismatch"],
            parse_ok=True,
        ),
    )
    calls = {"n": 0}

    def fake_loop(*a, **k):  # noqa: ANN003
        calls["n"] += 1
        assert k.get("model") == "qwen3:32b"
        assert k.get("initial_feedback")
        # feedback must not prescribe ops
        fb = " ".join(k["initial_feedback"])
        assert "remove aggregate" not in fb.lower()
        assert "group by" not in fb.lower() or "observability" in fb.lower()
        return IntegrationPipelineResult(
            status="success",
            plan=_plan("detail"),
            metadata={"attempt_count": 1, "retry_count": 0},
        )

    monkeypatch.setattr(
        "core.integrate.semantic_escalation._run_integration_attempt_loop", fake_loop
    )
    monkeypatch.setattr(
        "core.integrate.semantic_escalation._merge_escalation_result",
        lambda fast, strong, decision, strategy: strong,
    )

    out = run_integration_pipeline_semantic_experimental(
        "stack compatible rows without totals",
        {"a": pd.DataFrame({"id": [1]}), "b": pd.DataFrame({"id": [2]})},
        {"file_profiles": [], "relationships": []},
        config=SemanticEscalationConfig(enable_failure_escalation=False),
    )
    assert calls["n"] == 1
    assert MAX_SEMANTIC_ESCALATIONS == 1
    assert out.metadata.get("semantic_escalation_32b") is True


def test_uncertain_policy_explicit(monkeypatch) -> None:
    plan = _plan("group")
    base = IntegrationPipelineResult(status="success", plan=plan, metadata={})
    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_integration_pipeline",
        lambda *a, **k: base,
    )
    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_semantic_verification",
        lambda **k: SemanticVerificationResult(
            verdict="uncertain",
            reason_code="insufficient_evidence",
            evidence=[],
            parse_ok=True,
        ),
    )
    calls = {"n": 0}

    def fake_loop(*a, **k):  # noqa: ANN003
        calls["n"] += 1
        return IntegrationPipelineResult(status="success", plan=plan, metadata={})

    monkeypatch.setattr(
        "core.integrate.semantic_escalation._run_integration_attempt_loop",
        fake_loop,
    )
    monkeypatch.setattr(
        "core.integrate.semantic_escalation._merge_escalation_result",
        lambda fast, strong, decision, strategy: strong,
    )

    run_integration_pipeline_semantic_experimental(
        "x",
        {"a": pd.DataFrame()},
        {},
        config=SemanticEscalationConfig(
            enable_failure_escalation=False, uncertain_policy="accept"
        ),
    )
    assert calls["n"] == 0

    run_integration_pipeline_semantic_experimental(
        "x",
        {"a": pd.DataFrame()},
        {},
        config=SemanticEscalationConfig(
            enable_failure_escalation=False, uncertain_policy="escalate"
        ),
    )
    assert calls["n"] == 1


def test_semantic_feedback_no_prescriptive_repair() -> None:
    fb = build_semantic_replan_feedback(
        previous_plan=_plan("group"),
        verification=SemanticVerificationResult(
            verdict="fail",
            reason_code="wrong_output_grain",
            evidence=["collapsed"],
        ),
    )
    text = " ".join(fb).lower()
    for banned in ("remove aggregate", "use union", "include customer_name", "group by x"):
        assert banned not in text
    assert "semantically inconsistent" in text


def test_variant_is_v1_only() -> None:
    assert SEMANTIC_VERIFIER_VARIANT == "V1"


def test_no_scenario_routing_in_module() -> None:
    from pathlib import Path

    src = Path("core/integrate/semantic_escalation.py").read_text(encoding="utf-8")
    for banned in ("same_schema", "three_file", "composite_key", "customer_name", "scenario =="):
        assert banned not in src
