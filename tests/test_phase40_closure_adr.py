"""Phase 40 Closure freeze: docs + production boundary. No semantic behavior change."""

from __future__ import annotations

from pathlib import Path

from core.integrate.schema_lineage import observe_final_grain_identities
from core.integrate.semantic_escalation import SEMANTIC_VERIFIER_MODEL, SEMANTIC_VERIFIER_VARIANT
from core.integrate.result_observation import MAX_RESULT_SAMPLE_ROWS
from core.shadow.config import load_shadow_config


def test_closure_docs_and_frozen_tokens() -> None:
    adr = Path("docs/architecture/adr_phase40_semantic_reliability_strategy.md").read_text()
    note = Path("docs/learning_note/phase40_closure_semantic_reliability_research.md").read_text()
    for text in (adr, note):
        assert "KEEP_CURRENT_PRODUCTION_ARCHITECTURE" in text
        assert "NO_SAFE_OPERATIONAL_STRATEGY" in text
        assert "KEEP_7B_CURRENT" in text
        assert "NOT_APPROVED" in text
        assert "PHASE_40_RESEARCH = CLOSED" in text
        assert "ACTUAL_STRONG_RECOVERY" in text
        assert "OBSERVER_REANALYSIS_ONLY" in text
        assert "RETAIN_AS_GENERIC_INFRASTRUCTURE" in text


def test_production_boundary_unchanged() -> None:
    assert SEMANTIC_VERIFIER_MODEL == "qwen2.5:7b"
    assert SEMANTIC_VERIFIER_VARIANT == "V1"
    assert MAX_RESULT_SAMPLE_ROWS == 5
    assert not load_shadow_config().enabled
    assert callable(observe_final_grain_identities)
    for rel in (
        "core/integrate/integration_planner.py",
        "core/integrate/integration_plan_types.py",
        "core/integrate/integration_plan_validate.py",
        "core/integrate/semantic_escalation.py",
        "core/integrate/semantic_verifier.py",
        "core/integrate/schema_lineage.py",
    ):
        text = Path(rel).read_text()
        assert "SemanticRequirementContract" not in text
        assert "PHASE40H" not in text
        assert "ContractPlanChecker" not in text
    lin = Path("core/integrate/schema_lineage.py").read_text()
    assert "def observe_final_grain_identities" in lin
    # Observer is not attached to the verifier payload builder.
    assert "observe_final_grain_identities" not in Path("core/integrate/semantic_verifier.py").read_text()
    assert "observe_final_grain_identities" not in Path("core/integrate/semantic_escalation.py").read_text()
