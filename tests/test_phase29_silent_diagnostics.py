"""Phase 29: silent wrong-success diagnostics tests (no production outcome change)."""

from __future__ import annotations

from pathlib import Path

from core.integrate.integration_plan_types import (
    FinalOutputRequirements,
    IntegrationPlan,
    IntegrationStep,
)
from core.integrate.planner_model_strategy import (
    PlannerModelStrategy,
    should_escalate_after_fast_path,
)
from core.integrate.result_diagnostics import observe_plan_diagnostics


def test_row_grain_with_aggregate_flag() -> None:
    plan = IntegrationPlan(
        status="planned",
        steps=[
            IntegrationStep(
                id="j",
                op="join",
                inputs=["a", "b"],
                output="j",
                params={"left_keys": ["k"], "right_keys": ["k"], "how": "inner"},
            ),
            IntegrationStep(
                id="agg",
                op="aggregate",
                inputs=["j"],
                output="out",
                params={"group_by": ["k"], "metrics": [{"column": "x", "function": "sum", "alias": "sx"}]},
            ),
        ],
        final_output="out",
        final_output_requirements=FinalOutputRequirements(
            grain="entity",
            required_columns=["k", "sx"],
        ),
    )
    d = observe_plan_diagnostics(plan)
    assert d.row_grain_with_collapsing_aggregate is True
    assert d.has_aggregate is True


def test_group_grain_with_aggregate_not_flagged_as_row_contradiction() -> None:
    plan = IntegrationPlan(
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
                params={"group_by": ["k"], "metrics": [{"column": "x", "function": "sum", "alias": "sx"}]},
            ),
        ],
        final_output="out",
        final_output_requirements=FinalOutputRequirements(
            grain="group",
            required_columns=["k", "sx"],
        ),
    )
    d = observe_plan_diagnostics(plan)
    assert d.row_grain_with_collapsing_aggregate is False


def test_diagnostics_do_not_mutate_plan() -> None:
    plan = IntegrationPlan(
        status="planned",
        steps=[
            IntegrationStep(id="s", op="select_columns", inputs=["a"], output="o", params={"columns": ["x"]}),
        ],
        final_output="o",
        final_output_requirements=FinalOutputRequirements(grain="detail", required_columns=["x"]),
    )
    before = plan.to_dict()
    observe_plan_diagnostics(plan)
    assert plan.to_dict() == before


def test_diagnostics_module_has_no_scenario_routing() -> None:
    text = Path("core/integrate/result_diagnostics.py").read_text(encoding="utf-8")
    for banned in (
        "three_file",
        "composite_key",
        "same_schema",
        "budget",
        "overall_ok",
        "case_id",
        "file_count",
    ):
        assert banned not in text


def test_phase28_escalation_policy_unchanged() -> None:
    """Diagnostics must not alter escalation decisions."""
    strategy = PlannerModelStrategy(enable_escalation=True)
    d = should_escalate_after_fast_path(
        status="failed",
        retry_log=[
            {
                "failure_stage": "integration_plan_validation",
                "failure_codes": ["join_key_dropped_in_final_projection"],
            }
        ],
        metadata={"exhausted": True, "plan_validation_failure_count": 1},
        strategy=strategy,
    )
    assert d.should_escalate is True
    d2 = should_escalate_after_fast_path(
        status="success",
        retry_log=[],
        metadata={},
        strategy=strategy,
    )
    assert d2.should_escalate is False


def test_phase29_harness_writes_artifacts(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    import tests.benchmark_multi.phase29_diagnostics as harness

    # Skip if frozen artifacts missing
    if not Path("benchmark_results/multi/phase27/qwen2.5_7b/full_19").is_dir():
        return
    monkeypatch.setattr(harness, "OUT", tmp_path)
    harness.main()
    for name in (
        "silent_failure_traces.json",
        "failure_taxonomy.json",
        "contract_coverage_audit.json",
        "candidate_invariants.json",
        "invariant_counterexamples.json",
        "observability_matrix.json",
        "phase29_kpis.json",
    ):
        assert (tmp_path / name).is_file()
