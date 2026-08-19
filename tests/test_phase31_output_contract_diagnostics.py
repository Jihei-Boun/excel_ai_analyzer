"""Phase 31: output-contract diagnostics tests (no production outcome change)."""

from __future__ import annotations

from core.integrate.integration_plan_types import (
    FinalOutputRequirements,
    IntegrationPlan,
    IntegrationStep,
)
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.output_contract_diagnostics import observe_output_contract
from core.integrate.planner_model_strategy import _ESCALATION_TRIGGER_CODES


def _minimal_understanding() -> dict:
    return {
        "file_profiles": [
            {
                "source_id": "orders",
                "observations": {
                    "columns": [
                        {"name": "customer_id", "dtype_family": "string"},
                        {"name": "product_id", "dtype_family": "string"},
                        {"name": "order_amount", "dtype_family": "number"},
                    ]
                },
            },
            {
                "source_id": "products",
                "observations": {
                    "columns": [
                        {"name": "product_id", "dtype_family": "string"},
                        {"name": "category_name", "dtype_family": "string"},
                    ]
                },
            },
            {
                "source_id": "customers",
                "observations": {
                    "columns": [
                        {"name": "customer_id", "dtype_family": "string"},
                        {"name": "customer_name", "dtype_family": "string"},
                    ]
                },
            },
        ],
        "relationships": [],
    }


def test_output_contract_diagnostics_reads_declarations() -> None:
    plan = IntegrationPlan(
        status="planned",
        steps=[
            IntegrationStep(
                id="j",
                op="join",
                inputs=["orders", "products"],
                output="op",
                params={"left_keys": ["product_id"], "right_keys": ["product_id"], "how": "left"},
            ),
            IntegrationStep(
                id="a",
                op="aggregate",
                inputs=["op"],
                output="out",
                params={
                    "group_by": ["customer_id", "category_name"],
                    "metrics": [
                        {
                            "column": "order_amount",
                            "function": "sum",
                            "alias": "total_order_amount",
                        }
                    ],
                },
            ),
        ],
        final_output="out",
        final_output_requirements=FinalOutputRequirements(
            grain="group",
            required_columns=["customer_id", "category_name", "total_order_amount"],
            one_row_represents="customer x category total",
        ),
    )
    d = observe_output_contract(
        plan, known_source_ids=["orders", "products", "customers"]
    )
    assert d.has_final_output_requirements
    assert d.declared_grain == "group"
    assert "customer_id" in d.declared_required_columns
    assert d.source_ids_referenced == ["orders", "products"]
    assert d.required_columns_subset_of_group_by_or_metrics is True


def test_diagnostics_do_not_mutate_plan() -> None:
    plan = IntegrationPlan(
        status="planned",
        steps=[
            IntegrationStep(
                id="u",
                op="union_rows",
                inputs=["a", "b"],
                output="u",
                params={"column_policy": "aligned"},
            )
        ],
        final_output="u",
        final_output_requirements=FinalOutputRequirements(
            grain="detail", required_columns=["x"]
        ),
    )
    before = plan.to_dict()
    observe_output_contract(plan)
    assert plan.to_dict() == before


def test_no_scenario_or_domain_hardcoding_in_module_source() -> None:
    from pathlib import Path

    src = Path("core/integrate/output_contract_diagnostics.py").read_text(encoding="utf-8")
    for banned in (
        "three_file",
        "same_schema",
        "customer_name",
        "product_id",
        "budget",
    ):
        assert banned not in src


def test_phase30_grain_blocking_unchanged() -> None:
    und = _minimal_understanding()
    plan = IntegrationPlan(
        status="planned",
        steps=[
            IntegrationStep(
                id="j",
                op="join",
                inputs=["orders", "products"],
                output="op",
                params={"left_keys": ["product_id"], "right_keys": ["product_id"], "how": "left"},
            ),
            IntegrationStep(
                id="a",
                op="aggregate",
                inputs=["op"],
                output="out",
                params={
                    "group_by": ["customer_id"],
                    "metrics": [
                        {"column": "order_amount", "function": "sum", "alias": "sx"}
                    ],
                },
            ),
        ],
        final_output="out",
        final_output_requirements=FinalOutputRequirements(
            grain="entity", required_columns=["customer_id", "sx"]
        ),
    )
    val = validate_integration_plan(und, plan)
    assert any(e.code == "final_grain_contradiction" for e in val.errors)


def test_escalation_triggers_unchanged_phase31() -> None:
    assert "final_grain_contradiction" in _ESCALATION_TRIGGER_CODES
    assert "join_key_dropped_in_final_projection" in _ESCALATION_TRIGGER_CODES
    # Phase 31 must not add Type-B-specific escalation routing
    assert "required_field_under_declaration" not in _ESCALATION_TRIGGER_CODES
