"""Phase 32: planner output-contract prompt tests (no semantic hardcoding)."""

from __future__ import annotations

from core.integrate.integration_planner import (
    _PLANNER_SYSTEM,
    _PLANNER_SYSTEM_BASELINE,
    _PLANNER_SYSTEM_CANDIDATE_A,
    get_planner_system_prompt,
)
from core.integrate.integration_plan_types import FinalOutputRequirements
from core.integrate.planner_model_strategy import _ESCALATION_TRIGGER_CODES


def test_candidate_contains_output_first_discipline() -> None:
    text = _PLANNER_SYSTEM_CANDIDATE_A
    assert "Answer fields vs mechanics" in text
    assert "ANSWER COMPLETENESS" in text
    assert "required_columns" in text
    assert "Do NOT dump every available column" in text


def test_candidate_has_no_benchmark_specific_terms() -> None:
    text = _PLANNER_SYSTEM_CANDIDATE_A.lower()
    for banned in (
        "three_file",
        "composite_key_join",
        "same_schema_union",
        "customer_name",
        "product_id",
        "budget",
        "orders.xlsx",
    ):
        assert banned not in text


def test_required_columns_schema_unchanged() -> None:
    req = FinalOutputRequirements(
        grain="group",
        required_columns=["a", "b"],
        one_row_represents="x",
    )
    d = req.to_dict()
    assert set(d.keys()) == {"grain", "required_columns", "one_row_represents"}


def test_escalation_triggers_unchanged() -> None:
    assert "final_grain_contradiction" in _ESCALATION_TRIGGER_CODES
    assert "required_field_under_declaration" not in _ESCALATION_TRIGGER_CODES


def test_prompt_variants_resolvable() -> None:
    assert get_planner_system_prompt(variant="baseline") == _PLANNER_SYSTEM_BASELINE
    assert get_planner_system_prompt(variant="candidate_a") == _PLANNER_SYSTEM_CANDIDATE_A
    # Production remains baseline until Phase 32 adopts
    assert _PLANNER_SYSTEM == _PLANNER_SYSTEM_BASELINE
