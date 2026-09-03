"""Post-Phase-40 Step 3 root-cause harness freeze (research only).

No live models. Confirms production baseline, DSL witnesses, and
cannot_plan skip-escalation policy as observed (not changed).
"""

from __future__ import annotations

from pathlib import Path

from core.integrate.planner_model_strategy import (
    PlannerModelStrategy,
    should_escalate_after_fast_path,
)
from core.integrate.semantic_escalation import SEMANTIC_VERIFIER_MODEL, SEMANTIC_VERIFIER_VARIANT
from core.shadow.config import load_shadow_config
from tests.benchmark_multi.phase40_residual import HEAD_EXPECTED, production_config
from tests.benchmark_multi.phase40_residual_rootcause import (
    evaluate_all_reference_plans,
    evidence_row,
)
from tests.benchmark_multi.phase40_residual_rootcause_plans import (
    TARGET_IDS,
    all_diagnostic_cases,
    build_control_cases,
    reference_plans,
)


def test_production_untouched_and_freeze_tokens() -> None:
    assert SEMANTIC_VERIFIER_MODEL == "qwen2.5:7b"
    assert SEMANTIC_VERIFIER_VARIANT == "V1"
    assert not load_shadow_config().enabled
    cfg = production_config()
    assert cfg.verifier_model == "qwen2.5:7b"
    assert cfg.strong_model == "qwen3:32b"
    assert cfg.reverify_strong is False
    assert HEAD_EXPECTED == "1911ed701c56d20593bb19bee752bc66ff1c4ed0"
    for rel in (
        "core/integrate/integration_planner.py",
        "core/integrate/integration_plan_validate.py",
        "core/integrate/integration_execute.py",
        "core/integrate/semantic_escalation.py",
        "core/integrate/planner_model_strategy.py",
        "core/routing/route_multi.py",
    ):
        text = Path(rel).read_text()
        assert "SemanticRequirementContract" not in text
        assert "r40-B02" not in text
        assert "CTRL-D03-SPLIT" not in text


def test_target_and_control_scope() -> None:
    assert TARGET_IDS == (
        "r40-B02",
        "r40-D03",
        "r40-D04",
        "r40-D01",
        "r40-F01",
        "r40-F03",
        "r40-G01",
        "r40-G03",
        "r40-F02",
    )
    controls = build_control_cases()
    assert 4 <= len(controls) <= 8
    ids = [c["case_id"] for c in all_diagnostic_cases()]
    assert len(ids) == len(set(ids))
    assert set(TARGET_IDS).issubset(set(ids))


def test_cannot_plan_skips_failure_escalation_policy() -> None:
    """Observes frozen policy: cannot_plan is not escalated to 32B."""
    decision = should_escalate_after_fast_path(
        status="cannot_plan",
        retry_log=[
            {
                "failure_codes": ["final_grain_contradiction"],
                "failure_stage": "integration_plan_validation",
            }
        ],
        metadata={"exhausted": True, "plan_validation_failure_count": 1},
        strategy=PlannerModelStrategy(enable_escalation=True, strong_model="qwen3:32b"),
    )
    assert decision.should_escalate is False
    assert decision.reason_code == "skip_cannot_plan"


def test_reference_plans_validate_execute_and_match_manual() -> None:
    plans = reference_plans()
    for cid in TARGET_IDS:
        assert cid in plans
    rows = evaluate_all_reference_plans()
    by_id = {r["case_id"]: r for r in rows}
    for cid in TARGET_IDS:
        row = by_id[cid]
        assert row["validator_valid"] is True, cid
        assert row["executor_success"] is True, cid
        assert row["result_validator_valid"] is True, cid
        assert row["manual"]["ok"] is True, (cid, row["manual"])


def test_h1_h2_rejected_on_deterministic_profiles() -> None:
    for case in all_diagnostic_cases():
        if case["case_id"] not in TARGET_IDS:
            continue
        ev = evidence_row(case)
        assert ev["h1_input_missing"] == "REJECTED", case["case_id"]
        assert ev["h2_understanding_distortion"] == "REJECTED", case["case_id"]
