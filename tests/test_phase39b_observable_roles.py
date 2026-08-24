"""Phase 39B guardrails: output_roles contract + independent verifier shaping."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pandas as pd
import pytest

from core.integrate.integration_plan_types import (
    FinalOutputRequirements,
    OutputRole,
    IntegrationPlanParseError,
    integration_plan_from_dict,
)
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.relationship_infer import build_cross_file_understanding
from core.integrate.semantic_verifier import (
    VERIFIER_SYSTEM_PROMPT,
    build_verifier_payload,
)


def _und(cols: list[str] | None = None) -> dict:
    cols = cols or ["entity_id", "value"]
    a = pd.DataFrame(
        {c: ([1, 2] if c.endswith("id") else [10, 20]) for c in cols}
    )
    b = pd.DataFrame(
        {c: ([1, 3] if c.endswith("id") else [11, 30]) for c in cols}
    )
    return build_cross_file_understanding(
        [("a.xlsx", a), ("b.xlsx", b)], infer_relationships=False
    ).to_dict()


def test_g1_plan_without_output_roles_still_parses() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "u",
            "final_output_requirements": {
                "grain": "detail",
                "required_columns": ["entity_id", "value"],
                "one_row_represents": "stacked row",
            },
            "steps": [
                {
                    "id": "s1",
                    "op": "union_rows",
                    "inputs": ["a.xlsx", "b.xlsx"],
                    "output": "u",
                    "params": {"column_policy": "aligned"},
                }
            ],
        }
    )
    assert plan.final_output_requirements is not None
    assert plan.final_output_requirements.output_roles == []


def test_g4_missing_role_columns_are_structural_errors() -> None:
    req = FinalOutputRequirements(
        grain="group",
        required_columns=["entity_id", "side_a", "side_b"],
        output_roles=[
            OutputRole(role="entity_key", columns=["entity_id"]),
            OutputRole(role="comparison_side", side_id="A", columns=["side_a"]),
            OutputRole(role="comparison_side", side_id="B", columns=["side_b"]),
        ],
    )
    assert req.to_dict()["output_roles"][1]["side_id"] == "A"

    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "final",
            "final_output_requirements": {
                "grain": "group",
                "required_columns": ["entity_id", "total_value"],
                "output_roles": [
                    {"role": "entity_key", "columns": ["entity_id"]},
                    {
                        "role": "comparison_side",
                        "side_id": "A",
                        "columns": ["side_a"],
                    },
                    {
                        "role": "comparison_side",
                        "side_id": "B",
                        "columns": ["side_b"],
                    },
                ],
            },
            "steps": [
                {
                    "id": "s1",
                    "op": "union_rows",
                    "inputs": ["a.xlsx", "b.xlsx"],
                    "output": "u",
                    "params": {"column_policy": "aligned"},
                },
                {
                    "id": "s2",
                    "op": "aggregate",
                    "inputs": ["u"],
                    "output": "final",
                    "params": {
                        "group_by": ["entity_id"],
                        "metrics": [
                            {
                                "column": "value",
                                "function": "sum",
                                "alias": "total_value",
                            }
                        ],
                    },
                },
            ],
        }
    )
    val = validate_integration_plan(_und(), plan)
    codes = {e.code for e in val.errors}
    assert "output_role_column_missing" in codes or "output_role_not_in_required_columns" in codes


def test_g2_g3_validator_does_not_require_roles_for_union_aggregate() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "final",
            "final_output_requirements": {
                "grain": "group",
                "required_columns": ["entity_id", "total_value"],
                "one_row_represents": "entity total",
            },
            "steps": [
                {
                    "id": "s1",
                    "op": "union_rows",
                    "inputs": ["a.xlsx", "b.xlsx"],
                    "output": "u",
                    "params": {"column_policy": "aligned"},
                },
                {
                    "id": "s2",
                    "op": "aggregate",
                    "inputs": ["u"],
                    "output": "final",
                    "params": {
                        "group_by": ["entity_id"],
                        "metrics": [
                            {
                                "column": "value",
                                "function": "sum",
                                "alias": "total_value",
                            }
                        ],
                    },
                },
            ],
        }
    )
    val = validate_integration_plan(_und(), plan)
    codes = {e.code for e in val.errors}
    assert "output_role_comparison_sides_insufficient" not in codes
    assert val.valid, [(e.code, e.message) for e in val.errors]


def test_insufficient_comparison_sides_when_declared() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "final",
            "final_output_requirements": {
                "grain": "group",
                "required_columns": ["entity_id", "total_value"],
                "output_roles": [
                    {"role": "entity_key", "columns": ["entity_id"]},
                    {
                        "role": "comparison_side",
                        "side_id": "A",
                        "columns": ["total_value"],
                    },
                ],
            },
            "steps": [
                {
                    "id": "s1",
                    "op": "aggregate",
                    "inputs": ["a.xlsx"],
                    "output": "final",
                    "params": {
                        "group_by": ["entity_id"],
                        "metrics": [
                            {
                                "column": "value",
                                "function": "sum",
                                "alias": "total_value",
                            }
                        ],
                    },
                }
            ],
        }
    )
    val = validate_integration_plan(_und(), plan)
    assert any(
        e.code == "output_role_comparison_sides_insufficient" for e in val.errors
    )


def test_g5_g6_verifier_payload_separates_claims() -> None:
    plan = {
        "status": "planned",
        "steps": [
            {
                "id": "s1",
                "op": "union_rows",
                "inputs": ["a", "b"],
                "output": "u",
                "params": {},
            },
            {
                "id": "s2",
                "op": "aggregate",
                "inputs": ["u"],
                "output": "final",
                "params": {
                    "group_by": ["region"],
                    "metrics": [
                        {
                            "column": "sales_amount",
                            "function": "sum",
                            "alias": "total_sales",
                        }
                    ],
                },
            },
        ],
        "final_output": "final",
        "final_output_requirements": {
            "grain": "group",
            "required_columns": ["region", "total_sales"],
            "one_row_represents": "sales comparison by region",
        },
        "reason": "this is a comparison",
        "notes": ["comparison done"],
    }
    payload = build_verifier_payload(
        user_prompt="compare sides and which increased",
        plan=plan,
        variant="V1",
        independent=True,
    )
    assert "plan_structure" in payload
    assert "planner_claims" in payload
    structure_s = json.dumps(payload["plan_structure"])
    claims_s = json.dumps(payload["planner_claims"])
    assert "one_row_represents" not in structure_s
    assert "sales comparison" in claims_s
    assert "Independence protocol" in VERIFIER_SYSTEM_PROMPT
    assert "NEVER sufficient for pass" in VERIFIER_SYSTEM_PROMPT
    assert "MUST fail" in VERIFIER_SYSTEM_PROMPT


def test_g7_valid_dual_side_roles_parse() -> None:
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "final",
            "final_output_requirements": {
                "grain": "group",
                "required_columns": ["entity_id", "side_a_value", "side_b_value"],
                "one_row_represents": "entity with both sides",
                "output_roles": [
                    {"role": "entity_key", "columns": ["entity_id"]},
                    {
                        "role": "comparison_side",
                        "side_id": "A",
                        "columns": ["side_a_value"],
                    },
                    {
                        "role": "comparison_side",
                        "side_id": "B",
                        "columns": ["side_b_value"],
                    },
                ],
            },
            "steps": [
                {
                    "id": "s1",
                    "op": "aggregate",
                    "inputs": ["a.xlsx"],
                    "output": "a_agg",
                    "params": {
                        "group_by": ["entity_id"],
                        "metrics": [
                            {
                                "column": "value",
                                "function": "sum",
                                "alias": "side_a_value",
                            }
                        ],
                    },
                },
                {
                    "id": "s2",
                    "op": "aggregate",
                    "inputs": ["b.xlsx"],
                    "output": "b_agg",
                    "params": {
                        "group_by": ["entity_id"],
                        "metrics": [
                            {
                                "column": "value",
                                "function": "sum",
                                "alias": "side_b_value",
                            }
                        ],
                    },
                },
                {
                    "id": "s3",
                    "op": "join",
                    "inputs": ["a_agg", "b_agg"],
                    "output": "final",
                    "params": {
                        "left_keys": ["entity_id"],
                        "right_keys": ["entity_id"],
                        "how": "outer",
                    },
                },
            ],
        }
    )
    assert len(plan.final_output_requirements.output_roles) == 3


def test_g8_no_filename_month_domain_rules_in_phase39b_modules() -> None:
    roots = [
        Path("core/integrate/integration_plan_types.py"),
        Path("core/integrate/integration_plan_validate.py"),
        Path("core/integrate/integration_result_validate.py"),
        Path("core/integrate/semantic_verifier.py"),
    ]
    banned = [
        "filename_to_month",
        "parse_month_from_filename",
        'if "비교"',
        "if '비교'",
        "keyword_routing",
    ]
    for path in roots:
        text = path.read_text(encoding="utf-8")
        low = text.lower()
        for b in banned:
            assert b.lower() not in low, f"{path} contains banned token {b!r}"
    tree = ast.parse(Path("core/integrate/integration_plan_validate.py").read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "infer_comparison_from_prompt" not in names


def test_unknown_role_name_rejected() -> None:
    with pytest.raises(IntegrationPlanParseError):
        integration_plan_from_dict(
            {
                "status": "planned",
                "final_output": "final",
                "final_output_requirements": {
                    "grain": "group",
                    "required_columns": ["entity_id"],
                    "output_roles": [
                        {"role": "july_side", "columns": ["entity_id"]},
                    ],
                },
                "steps": [
                    {
                        "id": "s1",
                        "op": "select_columns",
                        "inputs": ["a.xlsx"],
                        "output": "final",
                        "params": {"columns": ["entity_id"]},
                    }
                ],
            }
        )
