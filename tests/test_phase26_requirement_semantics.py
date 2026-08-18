"""Phase 26: Requirement semantics & final projection reliability (LLM-free)."""

from __future__ import annotations

import pandas as pd

from core.integrate.integration_contracts import is_final_contract_failure
from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_plan_types import (
    final_contract_failure_family,
    integration_plan_from_dict,
)
from core.integrate.integration_plan_validate import validate_integration_plan
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


def test_one_row_represents_optional_roundtrip() -> None:
    a = pd.DataFrame({"id": [1], "x": [1]})
    b = pd.DataFrame({"id": [1], "y": [2]})
    und = _und_two(a, b)
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "j",
            "final_output_requirements": {
                "grain": "detail",
                "one_row_represents": "one matched row with attributes from both sides",
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
    assert plan.final_output_requirements is not None
    assert plan.final_output_requirements.one_row_represents.startswith("one matched")
    val = validate_integration_plan(und, plan)
    assert val.valid
    assert any(i.code == "final_one_row_represents_declared" for i in val.infos)


def test_no_identity_columns_field() -> None:
    from core.integrate.integration_plan_types import FinalOutputRequirements

    d = FinalOutputRequirements(grain="entity", required_columns=["id"]).to_dict()
    assert "identity_columns" not in d


def test_required_field_dropped_by_select() -> None:
    a = pd.DataFrame({"product_id": ["p1"], "amount": [1]})
    b = pd.DataFrame({"product_id": ["p1"], "category_name": ["c"]})
    und = _und_two(a, b, la="orders", lb="products")
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "out",
            "final_output_requirements": {
                "grain": "entity",
                "one_row_represents": "one enriched order row",
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
    assert any(i.code == "join_key_dropped_in_final_projection" for i in val.errors)


def test_join_key_drop_without_required_still_errors_for_entity_grain() -> None:
    """Entity grain + select dropping join keys is a projection contradiction."""
    a = pd.DataFrame({"product_id": ["p1"], "amount": [1]})
    b = pd.DataFrame({"product_id": ["p1"], "category_name": ["c"]})
    und = _und_two(a, b, la="orders", lb="products")
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "out",
            "final_output_requirements": {
                "grain": "entity",
                "required_columns": ["category_name", "amount"],
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
    assert any(i.code == "join_key_dropped_in_final_projection" for i in val.errors)


def test_select_keeping_join_key_ok() -> None:
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
                    "params": {
                        "columns": ["product_id", "category_name", "amount"]
                    },
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert val.valid, [e.message for e in val.errors]


def test_required_field_lost_by_aggregate() -> None:
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


def test_rename_then_required_materializable() -> None:
    a = pd.DataFrame({"Product ID": ["p1"], "QTY": [1]})
    b = pd.DataFrame({"product_id": ["p2"], "qty": [2]})
    und = _und_two(a, b, la="dirty_a", lb="dirty_b", rel="compatible_schema")
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "combined",
            "final_output_requirements": {
                "grain": "detail",
                "one_row_represents": "one stacked product row",
                "required_columns": ["product_id", "qty"],
            },
            "steps": [
                {
                    "op": "rename_columns",
                    "inputs": ["dirty_a"],
                    "output": "a2",
                    "params": {
                        "mapping": {"Product ID": "product_id", "QTY": "qty"}
                    },
                },
                {
                    "op": "union_rows",
                    "inputs": ["a2", "dirty_b"],
                    "output": "combined",
                    "params": {"column_policy": "aligned"},
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert val.valid, [e.message for e in val.errors]


def test_aggregate_alias_materializable() -> None:
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


def test_valid_detail_and_group_plans() -> None:
    a = pd.DataFrame({"id": [1], "x": [1]})
    b = pd.DataFrame({"id": [1], "y": [2]})
    und = _und_two(a, b)
    detail = integration_plan_from_dict(
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
    assert validate_integration_plan(und, detail).valid


def test_retry_feedback_structure_no_prescription() -> None:
    a = pd.DataFrame({"product_id": ["p1"], "amount": [1]})
    b = pd.DataFrame({"product_id": ["p1"], "category_name": ["c"]})
    und = _und_two(a, b, la="orders", lb="products")
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "out",
            "final_output_requirements": {
                "grain": "entity",
                "required_columns": ["product_id", "category_name"],
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
                    "params": {"columns": ["category_name"]},
                },
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    fb = "\n".join(
        format_integration_validation_feedback(val, previous_plan=plan.to_dict())
    )
    assert "final_requirement_preservation" in fb
    assert "Declared final requirement" in fb
    assert "Observed plan effect" in fb
    assert "Remove select" not in fb
    assert "add product_id" not in fb.lower()


def test_final_contract_failure_family() -> None:
    assert (
        final_contract_failure_family(["join_key_dropped_in_final_projection"])
        == "projection_failure_family"
    )
    assert is_final_contract_failure(["join_key_dropped_in_final_projection"])


def test_executor_immutable_no_repair() -> None:
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
    before = plan.to_dict()
    val = validate_integration_plan(und, plan)
    assert plan.to_dict() == before
    exe = execute_integration_plan({"L": a, "R": b}, plan, val)
    assert exe.success
    assert plan.to_dict() == before


def test_composite_join_only_regression() -> None:
    a = pd.DataFrame(
        {
            "store_id": [1, 1, 2, 2],
            "product_id": [1, 2, 1, 2],
            "units": [1, 2, 3, 4],
        }
    )
    b = pd.DataFrame(
        {
            "store_id": [1, 1, 2, 2],
            "product_id": [1, 2, 1, 2],
            "unit_price": [3.0, 4.0, 5.0, 6.0],
        }
    )
    und = _und_two(a, b, la="sales", lb="price")
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "j",
            "final_output_requirements": {
                "grain": "detail",
                "one_row_represents": "one store-product matched row",
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
                }
            ],
        }
    )
    val = validate_integration_plan(und, plan)
    assert val.valid, [e.message for e in val.errors]


def test_no_domain_decision_branch() -> None:
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "core" / "integrate"
    hits = []
    pat = re.compile(
        r"""if\s+.*(case_id\s*==|domain\s*==|==\s*['\"]budget['\"]|==\s*['\"]inventory['\"]|비용코드|집행액)"""
    )
    for path in root.glob("*.py"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if pat.search(line):
                hits.append(f"{path.name}:{i}:{line.strip()}")
    assert not hits, hits
