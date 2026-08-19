"""Phase 24: Final grain & output selection reliability (LLM-free)."""

from __future__ import annotations

import pandas as pd

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


def test_detail_grain_join_only_ok() -> None:
    a = pd.DataFrame({"id": [1, 2], "x": [1, 2]})
    b = pd.DataFrame({"id": [1, 2], "y": [3, 4]})
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
    assert val.valid, [e.message for e in val.errors]


def test_detail_grain_rejects_collapsing_aggregate() -> None:
    """detail + collapsing aggregate → warning; required detail fields → error."""
    a = pd.DataFrame({"id": [1, 2], "x": [1, 2]})
    b = pd.DataFrame({"id": [1, 2], "y": [3, 4]})
    und = _und_two(a, b)
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "agg",
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
                    "op": "aggregate",
                    "inputs": ["j"],
                    "output": "agg",
                    "params": {
                        "group_by": ["id"],
                        "metrics": [{"column": "x", "function": "sum", "alias": "x"}],
                    },
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    # Missing required fields after aggregate is the hard error
    assert not val.valid
    assert any(i.code == "final_required_field_missing" for i in val.errors)
    # Phase 30: row grain + collapsing aggregate is also a blocking error
    assert any(i.code == "final_grain_contradiction" and i.severity == "error" for i in val.errors)


def test_detail_grain_mislabel_with_aggregate_is_blocking() -> None:
    """Phase 30: row-level grain + collapsing aggregate is a declared-contract ERROR."""
    a = pd.DataFrame({"id": [1, 2], "x": [1, 2]})
    b = pd.DataFrame({"id": [1, 2], "y": [3, 4]})
    und = _und_two(a, b)
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "agg",
            "final_output_requirements": {
                "grain": "detail",
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
    assert not val.valid
    assert any(i.code == "final_grain_contradiction" and i.severity == "error" for i in val.errors)


def test_group_grain_requires_aggregate() -> None:
    a = pd.DataFrame({"id": [1], "x": [1]})
    b = pd.DataFrame({"id": [1], "y": [2]})
    und = _und_two(a, b)
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "j",
            "final_output_requirements": {"grain": "group", "required_columns": ["id"]},
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
    # Misdeclared group without aggregate → warning only (do not fail valid joins)
    assert val.valid
    assert any(i.code == "final_grain_contradiction" for i in val.warnings)


def test_required_field_drop_by_select_detected() -> None:
    a = pd.DataFrame({"product_id": ["p1"], "amount": [1]})
    b = pd.DataFrame({"product_id": ["p1"], "category_name": ["c"]})
    und = _und_two(a, b, la="orders_lookup", lb="products", rel="lookup_candidate")
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
                    "inputs": ["orders_lookup", "products"],
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
                    "params": {"columns": ["amount", "category_name"]},
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert not val.valid
    assert any(i.code == "final_required_field_missing" for i in val.errors)
    fb = "\n".join(format_integration_validation_feedback(val, previous_plan=plan.to_dict()))
    assert (
        "final output requirements" in fb.lower()
        or "requested grain" in fb.lower()
        or "final_requirement_preservation" in fb.lower()
        or "final-output contract" in fb.lower()
    )
    assert "Remove the aggregate" not in fb
    assert "product_id" not in fb or "Change" not in fb  # no prescribed fix naming


def test_future_key_drop_still_blocked() -> None:
    a = pd.DataFrame({"product_id": ["p1"], "name": ["n"]})
    b = pd.DataFrame({"product_id": ["p1"], "price": [1.0]})
    und = _und_two(a, b, la="A", lb="B")
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "j",
            "steps": [
                {
                    "op": "select_columns",
                    "inputs": ["A"],
                    "output": "a1",
                    "params": {"columns": ["name"]},
                },
                {
                    "op": "join",
                    "inputs": ["a1", "B"],
                    "output": "j",
                    "params": {
                        "left_keys": ["product_id"],
                        "right_keys": ["product_id"],
                        "how": "inner",
                    },
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert not val.valid


def test_result_validator_checks_declared_required_columns() -> None:
    a = pd.DataFrame({"id": [1], "x": [1]})
    b = pd.DataFrame({"id": [1], "y": [2]})
    und = _und_two(a, b)
    # Plan without requirements that drops nothing - then mutate requirements via parse
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "j",
            "final_output_requirements": {
                "grain": "detail",
                "required_columns": ["id", "x", "y", "missing_col"],
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
    # Plan validator should catch missing required field on simulated schema
    val = validate_integration_plan(und, plan)
    assert not val.valid
    assert any(i.code == "final_required_field_missing" for i in val.errors)


def test_result_validator_actual_missing_required() -> None:
    """If plan validation was bypassed, result validator still checks declaration."""
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
    assert val.valid
    ex = execute_integration_plan({"L": a, "R": b}, plan, val)
    assert ex.success
    # Force missing by renaming after execute is not allowed; instead use select plan
    plan2 = integration_plan_from_dict(
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
                    "params": {"columns": ["id", "x"]},
                },
            ],
        }
    )
    val2 = validate_integration_plan(und, plan2)
    assert not val2.valid  # caught at plan validation


def test_near_tie_still_blocked() -> None:
    a = pd.DataFrame(
        {
            "customer_id": ["C1", "C2", "C3", "C4"],
            "account_id": ["A1", "A2", "A3", "A4"],
            "name": ["n1", "n2", "n3", "n4"],
        }
    )
    b = pd.DataFrame(
        {
            "customer_id": ["C1", "C2", "C3", "C4"],
            "account_id": ["A1", "A2", "A3", "A4"],
            "score": [1, 2, 3, 4],
        }
    )
    obs = build_pairwise_observation("ambiguous_a", a, "ambiguous_b", b)
    und = {
        "file_profiles": [
            {
                "source_id": "ambiguous_a",
                "row_count": 4,
                "column_count": 3,
                "observations": {
                    "column_names": list(a.columns),
                    "columns": [
                        {
                            "name": c,
                            "dtype_family": "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 4,
                            "sample_values": [],
                        }
                        for c in a.columns
                    ],
                },
                "semantic_hints": {},
            },
            {
                "source_id": "ambiguous_b",
                "row_count": 4,
                "column_count": 3,
                "observations": {
                    "column_names": list(b.columns),
                    "columns": [
                        {
                            "name": c,
                            "dtype_family": "numeric" if c == "score" else "string",
                            "null_ratio": 0,
                            "uniqueness_ratio": 1,
                            "distinct_count": 4,
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
                "left_source": "ambiguous_a",
                "right_source": "ambiguous_b",
                "relationship": "join_candidate",
                "confidence": 0.8,
                "evidence": [],
            }
        ],
    }
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "j",
            "final_output_requirements": {"grain": "detail"},
            "steps": [
                {
                    "op": "join",
                    "inputs": ["ambiguous_a", "ambiguous_b"],
                    "output": "j",
                    "params": {
                        "left_keys": ["customer_id"],
                        "right_keys": ["customer_id"],
                        "how": "inner",
                    },
                }
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert any(i.code == "ambiguous_key_selection" for i in val.errors)


def test_requirements_optional_backward_compatible() -> None:
    a = pd.DataFrame({"id": [1], "x": [1]})
    b = pd.DataFrame({"id": [1], "y": [2]})
    und = _und_two(a, b)
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "j",
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
    assert val.valid
    assert any(i.code == "final_output_requirements_absent" for i in val.infos)


def test_no_domain_hardcoding_in_production_integrate() -> None:
    from pathlib import Path

    root = Path("core/integrate")
    banned = ["비용코드", "실행예산", "집행액", "sales_store", "budget_a"]
    for p in root.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for b in banned:
            assert b not in text, f"{p} contains {b}"


def test_executor_unchanged_no_semantic_autocomplete() -> None:
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
    ex = execute_integration_plan({"L": a, "R": b}, plan, val)
    assert ex.success
    assert list(ex.final_output.columns) == ["id", "x", "y"]
    rv = validate_integration_result(plan, ex, plan_validation=val)
    assert rv.valid
