"""Phase 28: evidence-based planner escalation tests (no live LLM)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.integrate.integration_pipeline import run_integration_pipeline
from core.integrate.integration_plan_types import (
    FinalOutputRequirements,
    IntegrationPlan,
    IntegrationStep,
)
from core.integrate.planner_model_strategy import (
    PlannerModelStrategy,
    build_escalation_feedback,
    should_escalate_after_fast_path,
)
from core.integrate.relationship_infer import build_cross_file_understanding
from tests.benchmark_multi import DATASETS_DIR
from tests.benchmark_multi.generate_datasets import ensure_datasets
from tests.benchmark_multi.schema import load_all_cases


def test_fast_success_does_not_escalate() -> None:
    strategy = PlannerModelStrategy(enable_escalation=True)
    d = should_escalate_after_fast_path(
        status="success",
        retry_log=[],
        metadata={"exhausted": False},
        strategy=strategy,
    )
    assert d.should_escalate is False
    assert d.reason_code == "skip_success"


def test_cannot_plan_does_not_escalate() -> None:
    strategy = PlannerModelStrategy(enable_escalation=True)
    d = should_escalate_after_fast_path(
        status="cannot_plan",
        retry_log=[],
        metadata={},
        strategy=strategy,
    )
    assert d.should_escalate is False
    assert d.reason_code == "skip_cannot_plan"


def test_retry_exhausted_recoverable_escalates() -> None:
    strategy = PlannerModelStrategy(enable_escalation=True)
    d = should_escalate_after_fast_path(
        status="failed",
        retry_log=[
            {
                "failure_stage": "integration_plan_validation",
                "failure_codes": ["join_key_dropped_in_final_projection"],
            },
            {
                "failure_stage": "integration_plan_generation",
                "failure_codes": ["repeated_plan", "repeated_integration_family"],
            },
        ],
        metadata={
            "exhausted": True,
            "plan_validation_failure_count": 2,
            "duplicate_plan_count": 1,
            "same_family_repeat_count": 1,
            "repeated_final_contract_failure": True,
        },
        strategy=strategy,
    )
    assert d.should_escalate is True
    assert d.to_model == "qwen3:32b"
    assert d.reason_code is not None


def test_incompatible_union_exhaustion_does_not_escalate() -> None:
    strategy = PlannerModelStrategy(enable_escalation=True)
    d = should_escalate_after_fast_path(
        status="failed",
        retry_log=[
            {
                "failure_stage": "integration_plan_validation",
                "failure_codes": ["union_incompatible_schema"],
            },
            {
                "failure_stage": "integration_plan_generation",
                "failure_codes": ["repeated_plan"],
            },
        ],
        metadata={"exhausted": True, "plan_validation_failure_count": 2, "duplicate_plan_count": 1},
        strategy=strategy,
    )
    assert d.should_escalate is False
    assert d.reason_code == "skip_expected_negative_structural"


def test_unsafe_only_failures_do_not_escalate() -> None:
    strategy = PlannerModelStrategy(enable_escalation=True)
    d = should_escalate_after_fast_path(
        status="failed",
        retry_log=[
            {
                "failure_stage": "integration_plan_validation",
                "failure_codes": ["many_to_many_join_risk"],
            }
        ],
        metadata={
            "exhausted": True,
            "plan_validation_failure_count": 1,
            "validator_blocked_unsafe_plan": True,
        },
        strategy=strategy,
    )
    assert d.should_escalate is False


def test_escalation_feedback_does_not_dictate_plan() -> None:
    decision = should_escalate_after_fast_path(
        status="failed",
        retry_log=[{"failure_stage": "integration_plan_validation", "failure_codes": ["join_key_dropped_in_final_projection"]}],
        metadata={"exhausted": True, "plan_validation_failure_count": 1},
        strategy=PlannerModelStrategy(enable_escalation=True),
    )
    fb = "\n".join(
        build_escalation_feedback(
            decision=decision,
            retry_log=[{"failure_stage": "integration_plan_validation", "failure_codes": ["join_key_dropped_in_final_projection"]}],
            metadata={"exhausted": True, "plan_validation_failure_count": 1},
        )
    )
    assert "join →" not in fb
    assert "Use join" not in fb
    assert "cannot_plan" in fb


def test_escalation_invokes_strong_after_fast_fail() -> None:
    """Recoverable fast failure → strong path; validators still applied."""
    calls: list[str] = []

    def build_plan(user_prompt, understanding, **kwargs):  # noqa: ANN001,ANN003
        model = kwargs.get("model") or ""
        calls.append(str(model))
        if str(model).startswith("fast"):
            # Join then drop join key in select → join_key_dropped_in_final_projection
            return IntegrationPlan(
                status="planned",
                steps=[
                    IntegrationStep(
                        id="j1",
                        op="join",
                        inputs=["orders", "products"],
                        output="joined",
                        params={
                            "left_keys": ["product_id"],
                            "right_keys": ["product_id"],
                            "how": "left",
                        },
                    ),
                    IntegrationStep(
                        id="s1",
                        op="select_columns",
                        inputs=["joined"],
                        output="final",
                        params={"columns": ["order_id", "qty", "category"]},
                    ),
                ],
                final_output="final",
                final_output_requirements=FinalOutputRequirements(
                    grain="entity",
                    required_columns=["order_id", "qty", "category"],
                    one_row_represents="an order with category",
                ),
            )
        return IntegrationPlan(
            status="cannot_plan",
            steps=[],
            final_output=None,
            reason="insufficient_evidence",
        )

    sources = {
        "orders": pd.DataFrame({"order_id": [1], "product_id": [10], "qty": [2]}),
        "products": pd.DataFrame({"product_id": [10], "category": ["A"]}),
    }
    und = build_cross_file_understanding(list(sources.items()), infer_relationships=False).to_dict()
    und["relationships"] = [
        {
            "left_dataset": "orders",
            "right_dataset": "products",
            "candidate_keys": [{"left": ["product_id"], "right": ["product_id"]}],
            "relationship_type": "many_to_one",
            "confidence": 0.9,
            "evidence": {"match_rate": 1.0},
        }
    ]
    strategy = PlannerModelStrategy(
        fast_model="fast-model",
        strong_model="strong-model",
        enable_escalation=True,
        strong_max_retries=0,
    )
    out = run_integration_pipeline(
        "enrich orders with category",
        sources,
        und,
        max_retries=0,
        model="fast-model",
        build_plan_fn=build_plan,
        model_strategy=strategy,
    )
    assert "fast-model" in calls
    assert "strong-model" in calls
    assert out.metadata.get("escalated") is True
    assert out.status == "cannot_plan"
    assert out.metadata.get("final_path") == "strong_escalation_cannot_plan"


def test_fast_success_skips_strong_model() -> None:
    calls: list[str] = []

    def build_plan(user_prompt, understanding, **kwargs):  # noqa: ANN001,ANN003
        calls.append(str(kwargs.get("model")))
        return IntegrationPlan(
            status="cannot_plan",
            steps=[],
            final_output=None,
            reason="unrelated",
        )

    sources = {"a": pd.DataFrame({"x": [1]}), "b": pd.DataFrame({"y": [2]})}
    und = build_cross_file_understanding(list(sources.items()), infer_relationships=False)
    strategy = PlannerModelStrategy(
        fast_model="fast-model",
        strong_model="strong-model",
        enable_escalation=True,
    )
    out = run_integration_pipeline(
        "do not invent a join",
        sources,
        und.to_dict(),
        max_retries=0,
        model="fast-model",
        build_plan_fn=build_plan,
        model_strategy=strategy,
    )
    assert calls == ["fast-model"]
    assert out.metadata.get("escalated") is False
    assert out.status == "cannot_plan"


def test_ambiguous_fixed_plan_no_escalation_on_cannot_plan() -> None:
    ensure_datasets(DATASETS_DIR, force=False)
    case = next(c for c in load_all_cases() if c.id == "ambiguous_keys_001")
    assert case.fixed_plan is not None
    sources = {
        Path(f).stem: pd.read_excel(DATASETS_DIR / f) for f in case.files
    }
    und = build_cross_file_understanding(
        list(sources.items()), infer_relationships=False
    ).to_dict()
    if case.fixed_relationships:
        und["relationships"] = list(case.fixed_relationships)

    def chat(prompt: str, **kwargs):  # noqa: ANN003
        del prompt, kwargs
        return dict(case.fixed_plan)

    strategy = PlannerModelStrategy(enable_escalation=True)
    out = run_integration_pipeline(
        case.prompt,
        sources,
        und,
        max_retries=0,
        chat_json_fn=chat,
        model=strategy.fast_model,
        model_strategy=strategy,
    )
    assert out.status == "cannot_plan"
    assert out.metadata.get("escalated") is False


def test_escalation_decision_does_not_mutate_plan_object() -> None:
    plan = IntegrationPlan(status="planned", steps=[], final_output=None)
    before = plan.to_dict()
    should_escalate_after_fast_path(
        status="failed",
        retry_log=[{"failure_codes": ["join_key_dropped_in_final_projection"]}],
        metadata={"exhausted": True, "plan_validation_failure_count": 1},
        strategy=PlannerModelStrategy(enable_escalation=True),
    )
    assert plan.to_dict() == before


def test_no_hardcoded_scenario_routing_in_strategy_module() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "core/integrate/planner_model_strategy.py").read_text(encoding="utf-8")
    for banned in (
        "three_file_chain",
        "composite_key_join",
        "lookup_join_001",
        "budget",
        "overall_ok",
    ):
        assert banned not in text
    # Must not route on file-count heuristics
    assert "len(sources)" not in text
    assert "file_count" not in text
    assert "expected_result" not in text


def test_offline_simulation_artifact_schema() -> None:
    from tests.benchmark_multi.phase28_offline_sim import simulate_run

    strategy = PlannerModelStrategy(enable_escalation=True)
    # Skip if Phase 27 artifacts absent
    p = Path("benchmark_results/multi/phase27/qwen2.5_7b/full_19/run1.json")
    if not p.is_file():
        return
    sim = simulate_run(1, strategy)
    assert "metrics" in sim
    assert "escalation_rate" in sim["metrics"]
    assert sim["metrics"]["unsafe_execution_rate"] == 0.0
