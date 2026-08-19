"""Phase 30: declared grain consistency hardening tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.planner_model_strategy import (
    PlannerModelStrategy,
    should_escalate_after_fast_path,
)
from core.integrate.relationship_infer import build_cross_file_understanding


def _und(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    und = build_cross_file_understanding(
        [("L", a), ("R", b)], infer_relationships=False
    ).to_dict()
    und["relationships"] = [
        {
            "left_dataset": "L",
            "right_dataset": "R",
            "candidate_keys": [{"left": ["id"], "right": ["id"]}],
            "relationship_type": "many_to_one",
            "confidence": 0.9,
            "evidence": {"match_rate": 1.0},
        }
    ]
    return und


def test_row_grain_collapsing_aggregate_is_error() -> None:
    und = _und(
        pd.DataFrame({"id": [1, 1], "x": [1, 2]}),
        pd.DataFrame({"id": [1], "y": [9]}),
    )
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "agg",
            "final_output_requirements": {
                "grain": "entity",
                "required_columns": ["id", "sx"],
            },
            "steps": [
                {
                    "op": "join",
                    "inputs": ["L", "R"],
                    "output": "j",
                    "params": {"left_keys": ["id"], "right_keys": ["id"], "how": "left"},
                },
                {
                    "op": "aggregate",
                    "inputs": ["j"],
                    "output": "agg",
                    "params": {
                        "group_by": ["id"],
                        "metrics": [{"column": "x", "function": "sum", "alias": "sx"}],
                    },
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert not val.valid
    assert any(e.code == "final_grain_contradiction" for e in val.errors)


def test_group_grain_join_aggregate_accepted() -> None:
    und = _und(
        pd.DataFrame({"id": [1, 1], "x": [1, 2]}),
        pd.DataFrame({"id": [1], "y": [9]}),
    )
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "agg",
            "final_output_requirements": {
                "grain": "group",
                "required_columns": ["id", "sx"],
            },
            "steps": [
                {
                    "op": "join",
                    "inputs": ["L", "R"],
                    "output": "j",
                    "params": {"left_keys": ["id"], "right_keys": ["id"], "how": "left"},
                },
                {
                    "op": "aggregate",
                    "inputs": ["j"],
                    "output": "agg",
                    "params": {
                        "group_by": ["id"],
                        "metrics": [{"column": "x", "function": "sum", "alias": "sx"}],
                    },
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert val.valid, [e.message for e in val.errors]


def test_union_aggregate_with_group_grain_accepted() -> None:
    a = pd.DataFrame({"id": [1], "x": [1]})
    b = pd.DataFrame({"id": [2], "x": [2]})
    und = build_cross_file_understanding(
        [("A", a), ("B", b)], infer_relationships=False
    ).to_dict()
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "agg",
            "final_output_requirements": {
                "grain": "group",
                "required_columns": ["id", "sx"],
            },
            "steps": [
                {
                    "op": "union_rows",
                    "inputs": ["A", "B"],
                    "output": "u",
                    "params": {"column_policy": "aligned"},
                },
                {
                    "op": "aggregate",
                    "inputs": ["u"],
                    "output": "agg",
                    "params": {
                        "group_by": ["id"],
                        "metrics": [{"column": "x", "function": "sum", "alias": "sx"}],
                    },
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert val.valid, [e.message for e in val.errors]


def test_multi_group_keys_and_select_after_aggregate_accepted() -> None:
    und = _und(
        pd.DataFrame({"id": [1], "g": ["a"], "x": [1]}),
        pd.DataFrame({"id": [1], "y": [2]}),
    )
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "out",
            "final_output_requirements": {
                "grain": "summary",
                "required_columns": ["id", "g", "sx"],
            },
            "steps": [
                {
                    "op": "join",
                    "inputs": ["L", "R"],
                    "output": "j",
                    "params": {"left_keys": ["id"], "right_keys": ["id"], "how": "inner"},
                },
                {
                    "op": "aggregate",
                    "inputs": ["j"],
                    "output": "agg",
                    "params": {
                        "group_by": ["id", "g"],
                        "metrics": [{"column": "x", "function": "sum", "alias": "sx"}],
                    },
                },
                {
                    "op": "select_columns",
                    "inputs": ["agg"],
                    "output": "out",
                    "params": {"columns": ["id", "g", "sx"]},
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert val.valid, [e.message for e in val.errors]


def test_renamed_group_key_then_aggregate_accepted() -> None:
    a = pd.DataFrame({"old_id": [1, 1], "x": [1, 2]})
    und = build_cross_file_understanding([("A", a)], infer_relationships=False).to_dict()
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "agg",
            "final_output_requirements": {
                "grain": "group",
                "required_columns": ["id", "sx"],
            },
            "steps": [
                {
                    "op": "rename_columns",
                    "inputs": ["A"],
                    "output": "r",
                    "params": {"mapping": {"old_id": "id"}},
                },
                {
                    "op": "aggregate",
                    "inputs": ["r"],
                    "output": "agg",
                    "params": {
                        "group_by": ["id"],
                        "metrics": [{"column": "x", "function": "sum", "alias": "sx"}],
                    },
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert val.valid, [e.message for e in val.errors]


def test_select_after_aggregate_still_blocks_row_grain() -> None:
    und = _und(
        pd.DataFrame({"id": [1], "x": [1]}),
        pd.DataFrame({"id": [1], "y": [2]}),
    )
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "out",
            "final_output_requirements": {"grain": "detail", "required_columns": ["id", "sx"]},
            "steps": [
                {
                    "op": "join",
                    "inputs": ["L", "R"],
                    "output": "j",
                    "params": {"left_keys": ["id"], "right_keys": ["id"], "how": "inner"},
                },
                {
                    "op": "aggregate",
                    "inputs": ["j"],
                    "output": "agg",
                    "params": {
                        "group_by": ["id"],
                        "metrics": [{"column": "x", "function": "sum", "alias": "sx"}],
                    },
                },
                {
                    "op": "select_columns",
                    "inputs": ["agg"],
                    "output": "out",
                    "params": {"columns": ["id", "sx"]},
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert not val.valid
    assert any(e.code == "final_grain_contradiction" for e in val.errors)


def test_no_scenario_hardcoding_in_validator_grain_block() -> None:
    text = Path("core/integrate/integration_plan_validate.py").read_text(encoding="utf-8")
    # Isolate Phase 30 blocking branch comment/code region
    start = text.find("Phase 30: blocking ERROR")
    assert start != -1
    region = text[start : start + 800]
    for banned in ("composite_key_join", "three_file_chain", "same_schema_union", "budget"):
        assert banned not in region
    assert "file_count" not in region
    assert "scenario ==" not in region


def test_grain_contradiction_escalation_trigger() -> None:
    d = should_escalate_after_fast_path(
        status="failed",
        retry_log=[
            {
                "failure_stage": "integration_plan_validation",
                "failure_codes": ["final_grain_contradiction"],
            }
        ],
        metadata={"exhausted": True, "plan_validation_failure_count": 1},
        strategy=PlannerModelStrategy(enable_escalation=True),
    )
    assert d.should_escalate is True


def test_type_c_consistent_group_not_blocked() -> None:
    """Internally consistent group+aggregate must remain valid (Type C boundary)."""
    a = pd.DataFrame({"product_id": [1, 1], "qty": [1, 2], "amount": [10, 20]})
    b = pd.DataFrame({"product_id": [2], "qty": [3], "amount": [30]})
    und = build_cross_file_understanding(
        [("Jan", a), ("Feb", b)], infer_relationships=False
    ).to_dict()
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "agg",
            "final_output_requirements": {
                "grain": "group",
                "required_columns": ["product_id", "total_qty", "total_amount"],
            },
            "steps": [
                {
                    "op": "union_rows",
                    "inputs": ["Jan", "Feb"],
                    "output": "u",
                    "params": {"column_policy": "aligned"},
                },
                {
                    "op": "aggregate",
                    "inputs": ["u"],
                    "output": "agg",
                    "params": {
                        "group_by": ["product_id"],
                        "metrics": [
                            {"column": "qty", "function": "sum", "alias": "total_qty"},
                            {
                                "column": "amount",
                                "function": "sum",
                                "alias": "total_amount",
                            },
                        ],
                    },
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert val.valid, [e.message for e in val.errors]
