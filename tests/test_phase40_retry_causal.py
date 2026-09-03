"""Post-Phase-40 Step 4 retry-causal freeze tests. No live models."""

from __future__ import annotations

from pathlib import Path

from core.integrate.planner_model_strategy import (
    PlannerModelStrategy,
    should_escalate_after_fast_path,
)
from core.integrate.semantic_escalation import SEMANTIC_VERIFIER_MODEL, SEMANTIC_VERIFIER_VARIANT
from core.shadow.config import load_shadow_config
from tests.benchmark_multi.phase40_residual import HEAD_EXPECTED
from tests.benchmark_multi.phase40_retry_causal import (
    FAMILY_LOCK_NEEDLE,
    FORBIDDEN_UNLOCK_HINTS,
    UNLOCKED_RETRY_LINE,
    state_machine_spec,
    unlock_family_lock_feedback,
)


def test_production_untouched() -> None:
    assert HEAD_EXPECTED == "1911ed701c56d20593bb19bee752bc66ff1c4ed0"
    assert SEMANTIC_VERIFIER_MODEL == "qwen2.5:7b"
    assert SEMANTIC_VERIFIER_VARIANT == "V1"
    assert not load_shadow_config().enabled
    for rel in (
        "core/integrate/integration_pipeline.py",
        "core/integrate/integration_planner.py",
        "core/integrate/integration_validation_types.py",
        "core/integrate/planner_model_strategy.py",
        "core/integrate/semantic_escalation.py",
    ):
        text = Path(rel).read_text()
        assert "COUNTERFACTUAL_STRONG_ESCALATION" not in text
        assert "UNLOCKED_RETRY_LINE" not in text
        assert "r40-B02" not in text
        assert "SemanticRequirementContract" not in text


def test_skip_cannot_plan_policy_frozen() -> None:
    skip = should_escalate_after_fast_path(
        status="cannot_plan",
        retry_log=[{"failure_codes": ["final_grain_contradiction"]}],
        metadata={"exhausted": True, "plan_validation_failure_count": 1},
        strategy=PlannerModelStrategy(enable_escalation=True, strong_model="qwen3:32b"),
    )
    assert skip.should_escalate is False
    assert skip.reason_code == "skip_cannot_plan"
    go = should_escalate_after_fast_path(
        status="failed",
        retry_log=[{"failure_codes": ["final_grain_contradiction"]}],
        metadata={"exhausted": True, "plan_validation_failure_count": 1},
        strategy=PlannerModelStrategy(enable_escalation=True, strong_model="qwen3:32b"),
    )
    assert go.should_escalate is True


def test_unlock_strips_only_family_lock() -> None:
    frozen = [
        "Failure stage: integration_plan_validation",
        "This is a structural_contract_failure. "
        "Prefer repairing the previous plan: keep the same integration strategy family "
        "when the composition matches the user request; fix only contract violations "
        "(missing/renamed columns, aliases, step outputs, params shape). "
        "Do not invent keys or swap to an unrelated strategy. "
        "Semantic operation sequence can remain; downstream references must match "
        "declared intermediate schemas.",
        "Code: final_grain_contradiction",
    ]
    unlocked = unlock_family_lock_feedback(frozen)
    assert not any(FAMILY_LOCK_NEEDLE in x for x in unlocked)
    assert UNLOCKED_RETRY_LINE in unlocked
    blob = "\n".join(unlocked).lower()
    for hint in FORBIDDEN_UNLOCK_HINTS:
        assert hint not in blob
    assert "actual" not in blob
    assert "forecast" not in blob
    assert "use join" not in blob


def test_keep_family_sentence_still_in_production() -> None:
    text = Path("core/integrate/integration_validation_types.py").read_text()
    assert FAMILY_LOCK_NEEDLE in text


def test_state_machine_mentions_parse_failed_and_skip() -> None:
    spec = state_machine_spec()
    blob = str(spec)
    assert "planner_parse_failed" in blob
    assert "skip_cannot_plan" in blob
    assert spec["defaults"]["pipeline_rounds"] == 3
