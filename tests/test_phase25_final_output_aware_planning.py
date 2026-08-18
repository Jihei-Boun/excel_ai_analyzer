"""Phase 25: Final-output-aware planning & requirement preservation (LLM-free)."""

from __future__ import annotations

import pandas as pd

from core.integrate.integration_contracts import (
    is_final_contract_failure,
    classify_integration_failure_codes,
)
from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.integration_result_validate import validate_integration_result
from core.integrate.integration_validation_types import format_integration_validation_feedback
from core.integrate.relationship_profile import build_pairwise_observation


def _und_two(a: pd.DataFrame, b: pd.DataFrame, *, la="L", lb="R", rel="join_candidate"):
    obs = build_pairwise_observation(la, a, lb, b)
    return {
        "file_profiles": [
            {
                "source_id": la,
                "row_count": len(a),
                "column_count": len(a.columns),
                "observations": {
                    "column_names": list(a.columns),
                    "columns": [
                        {
                            "name": c,
                            "dtype_family": "numeric"
                            if pd.api.types.is_numeric_dtype(a[c])
                            else "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": float(a[c].nunique() / max(len(a), 1)),
                            "distinct_count": int(a[c].nunique()),
                            "sample_values": [],
                        }
                        for c in a.columns
                    ],
                },
                "semantic_hints": {},
            },
            {
                "source_id": lb,
                "row_count": len(b),
                "column_count": len(b.columns),
                "observations": {
                    "column_names": list(b.columns),
                    "columns": [
                        {
                            "name": c,
                            "dtype_family": "numeric"
                            if pd.api.types.is_numeric_dtype(b[c])
                            else "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": float(b[c].nunique() / max(len(b), 1)),
                            "distinct_count": int(b[c].nunique()),
                            "sample_values": [],
                        }
                        for c in b.columns
                    ],
                },
                "semantic_hints": {},
            },
        ],
        "pairwise_observations": [obs.to_dict()],
        "relationships": [
            {
                "left_source": la,
                "right_source": lb,
                "relationship": rel,
                "confidence": 0.9,
                "evidence": [],
            }
        ],
    }


def test_required_field_permanent_loss_on_select() -> None:
    a = pd.DataFrame({"product_id": ["p1"], "amount": [1]})
    b = pd.DataFrame({"product_id": ["p1"], "category_name": ["c"]})
    und = _und_two(a, b, la="orders", lb="products")
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "out",
            "final_output_requirements": {
                "grain": "detail",
                "required_columns": ["product_id", "category_name", "amount"],
            },
            "steps": [
                {
                    "op": "join",
                    "inputs": ["orders", "products"],
                    "output": "j",
                    "params": {
                        "left_keys": ["product_id"],
                        "right_keys": ["product_id"],
                        "how": "left",
                    },
                },
                {
                    "op": "select_columns",
                    "inputs": ["j"],
                    "output": "out",
                    "params": {"columns": ["category_name", "amount"]},
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert not val.valid
    assert any(i.code == "final_required_field_missing" for i in val.errors)
    assert any(i.code == "required_field_permanently_lost" for i in val.errors)
    lost = next(i for i in val.errors if i.code == "required_field_permanently_lost")
    assert lost.details.get("lost_at_op") == "select_columns"


def test_aggregate_materializes_required_alias() -> None:
    a = pd.DataFrame({"id": [1, 1], "x": [1, 2]})
    b = pd.DataFrame({"id": [1], "y": [9]})
    und = _und_two(a, b)
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "agg",
            "final_output_requirements": {
                "grain": "group",
                "required_columns": ["id", "total_x"],
            },
            "steps": [
                {
                    "op": "join",
                    "inputs": ["L", "R"],
                    "output": "j",
                    "params": {
                        "left_keys": ["id"],
                        "right_keys": ["id"],
                        "how": "inner",
                    },
                },
                {
                    "op": "aggregate",
                    "inputs": ["j"],
                    "output": "agg",
                    "params": {
                        "group_by": ["id"],
                        "metrics": [
                            {"column": "x", "function": "sum", "alias": "total_x"}
                        ],
                    },
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert val.valid, [e.message for e in val.errors]


def test_strong_grain_contradiction_when_required_lost() -> None:
    a = pd.DataFrame({"store_id": [1], "product_id": [1], "units": [2]})
    b = pd.DataFrame({"store_id": [1], "product_id": [1], "unit_price": [3.0]})
    und = _und_two(a, b, la="sales", lb="price")
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "agg",
            "final_output_requirements": {
                "grain": "detail",
                "required_columns": ["store_id", "product_id", "units", "unit_price"],
            },
            "steps": [
                {
                    "op": "join",
                    "inputs": ["sales", "price"],
                    "output": "j",
                    "params": {
                        "left_keys": ["store_id", "product_id"],
                        "right_keys": ["store_id", "product_id"],
                        "how": "inner",
                    },
                },
                {
                    "op": "aggregate",
                    "inputs": ["j"],
                    "output": "agg",
                    "params": {
                        "group_by": ["store_id", "product_id"],
                        "metrics": [
                            {"column": "units", "function": "sum", "alias": "total_units"}
                        ],
                    },
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert not val.valid
    assert any(i.code == "final_required_field_missing" for i in val.errors)
    assert any(
        i.code == "final_grain_contradiction" and i.severity == "error"
        for i in val.errors
    )


def test_entity_group_by_identity_aggregate_allowed() -> None:
    """group_by=[entity id] with matching required fields is valid (warning for grain label ok)."""
    a = pd.DataFrame({"customer_id": [1, 1], "amount": [10, 20]})
    b = pd.DataFrame({"customer_id": [1], "name": ["A"]})
    und = _und_two(a, b, la="orders", lb="customers")
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "agg",
            "final_output_requirements": {
                "grain": "entity",
                "required_columns": ["customer_id", "total_amount"],
            },
            "steps": [
                {
                    "op": "join",
                    "inputs": ["orders", "customers"],
                    "output": "j",
                    "params": {
                        "left_keys": ["customer_id"],
                        "right_keys": ["customer_id"],
                        "how": "inner",
                    },
                },
                {
                    "op": "aggregate",
                    "inputs": ["j"],
                    "output": "agg",
                    "params": {
                        "group_by": ["customer_id"],
                        "metrics": [
                            {
                                "column": "amount",
                                "function": "sum",
                                "alias": "total_amount",
                            }
                        ],
                    },
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    # grain=entity + aggregate → warning only when required fields present
    assert val.valid, [e.message for e in val.errors]
    assert any(i.code == "final_grain_contradiction" for i in val.warnings)


def test_final_contract_failure_classification() -> None:
    assert is_final_contract_failure(["required_field_permanently_lost"])
    assert classify_integration_failure_codes(
        ["required_field_permanently_lost"]
    ) == "structural_contract_failure"


def test_retry_feedback_mentions_preservation_not_prescription() -> None:
    a = pd.DataFrame({"id": [1], "x": [1]})
    b = pd.DataFrame({"id": [1], "y": [2]})
    und = _und_two(a, b)
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "out",
            "final_output_requirements": {
                "grain": "detail",
                "required_columns": ["id", "x", "y"],
            },
            "steps": [
                {
                    "op": "join",
                    "inputs": ["L", "R"],
                    "output": "j",
                    "params": {
                        "left_keys": ["id"],
                        "right_keys": ["id"],
                        "how": "inner",
                    },
                },
                {
                    "op": "select_columns",
                    "inputs": ["j"],
                    "output": "out",
                    "params": {"columns": ["id"]},
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    fb = "\n".join(format_integration_validation_feedback(val, previous_plan=plan.to_dict()))
    assert "final_requirement_preservation" in fb
    assert "Remove select" not in fb
    assert "Add x" not in fb


def test_executor_does_not_restore_dropped_column() -> None:
    a = pd.DataFrame({"id": [1], "x": [1]})
    b = pd.DataFrame({"id": [1], "y": [2]})
    und = _und_two(a, b)
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "out",
            "final_output_requirements": {"grain": "detail", "required_columns": ["id", "y"]},
            "steps": [
                {
                    "op": "join",
                    "inputs": ["L", "R"],
                    "output": "j",
                    "params": {
                        "left_keys": ["id"],
                        "right_keys": ["id"],
                        "how": "inner",
                    },
                },
                {
                    "op": "select_columns",
                    "inputs": ["j"],
                    "output": "out",
                    "params": {"columns": ["id", "y"]},
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert val.valid
    exe = execute_integration_plan({"L": a, "R": b}, plan, val)
    assert exe.success
    assert list(exe.final_output.columns) == ["id", "y"]


def test_result_validator_checks_declared_not_prompt() -> None:
    a = pd.DataFrame({"id": [1], "x": [1]})
    b = pd.DataFrame({"id": [1], "y": [2]})
    und = _und_two(a, b)
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "j",
            "final_output_requirements": {
                "grain": "detail",
                "required_columns": ["id", "x", "y"],
            },
            "steps": [
                {
                    "op": "join",
                    "inputs": ["L", "R"],
                    "output": "j",
                    "params": {
                        "left_keys": ["id"],
                        "right_keys": ["id"],
                        "how": "inner",
                    },
                }
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    exe = execute_integration_plan({"L": a, "R": b}, plan, val)
    assert exe.success
    # Drop a declared column after the fact to simulate contract drift
    from core.integrate.integration_execution_types import IntegrationExecutionResult

    bad = IntegrationExecutionResult(
        success=True,
        final_output=exe.final_output.drop(columns=["y"]),
        final_output_name=exe.final_output_name,
        datasets=exe.datasets,
        step_results=exe.step_results,
        metadata=exe.metadata,
        lineage=exe.lineage,
    )
    rv = validate_integration_result(plan, bad, plan_validation=val)
    assert not rv.valid
    assert any(i.code == "final_required_column_missing" for i in rv.errors)


def test_no_identity_columns_contract_field() -> None:
    """Phase 25 chose not to add identity_columns — keep contract minimal."""
    from core.integrate.integration_plan_types import FinalOutputRequirements

    req = FinalOutputRequirements(grain="entity", required_columns=["id"])
    assert "identity_columns" not in req.to_dict()


def test_production_hardcoding_audit_phase25() -> None:
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "core" / "integrate"
    banned = re.compile(
        r"\b(customer_id|product_id|budget|inventory|warehouse|비용코드|집행액|예산)\b"
    )
    # Allow only non-decision mentions in comments is hard; scan for if/assert branches
    hits = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "if " in line and banned.search(line) and "test" not in path.name:
                # relationship labels / dtype checks may mention generic words — flag keyword branches
                if any(
                    k in line
                    for k in (
                        "customer_id ==",
                        "product_id ==",
                        '"budget"',
                        "'budget'",
                        "비용코드",
                        "집행액",
                    )
                ):
                    hits.append(f"{path.name}:{i}:{line.strip()}")
    assert not hits, hits
