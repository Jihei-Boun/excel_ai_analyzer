"""Phase 39U — Filter-aware join cardinality validation.

Deterministic. No LLM. No benchmark-family routing.
"""

from __future__ import annotations

import copy

import pandas as pd

from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.relationship_profile import build_file_profile, build_pairwise_observation


def _und_one(name: str, df: pd.DataFrame) -> dict:
    return {
        "file_profiles": [build_file_profile(name, df).to_dict()],
        "pairwise_observations": [],
        "relationships": [],
    }


def _und_two(la: str, a: pd.DataFrame, lb: str, b: pd.DataFrame, *, rel="join_candidate") -> dict:
    pair = build_pairwise_observation(la, a, lb, b)
    return {
        "file_profiles": [
            build_file_profile(la, a).to_dict(),
            build_file_profile(lb, b).to_dict(),
        ],
        "pairwise_observations": [pair.to_dict() if hasattr(pair, "to_dict") else pair],
        "relationships": [
            {
                "left_source": la,
                "right_source": lb,
                "relationship": rel,
                "key_candidates": [{"left_column": "k", "right_column": "k"}],
                "ambiguities": [],
            }
        ],
    }


def _d01_like_frames() -> tuple[dict[str, pd.DataFrame], dict, object]:
    src = "readings.xlsx"
    df = pd.DataFrame(
        {
            "entity_id": ["A", "A", "B", "B"],
            "part": ["P1", "P2", "P1", "P2"],
            "value": [10, 12, 8, 9],
        }
    )
    frames = {src: df}
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "reason": "independent declared filters then join",
            "final_output": "joined",
            "steps": [
                {
                    "id": "f1",
                    "op": "filter_rows",
                    "inputs": [src],
                    "output": "b1",
                    "params": {
                        "conditions": [{"column": "part", "operator": "eq", "value": "P1"}]
                    },
                },
                {
                    "id": "r1",
                    "op": "rename_columns",
                    "inputs": ["b1"],
                    "output": "b1r",
                    "params": {"mapping": {"value": "value_p1"}},
                },
                {
                    "id": "f2",
                    "op": "filter_rows",
                    "inputs": [src],
                    "output": "b2",
                    "params": {
                        "conditions": [{"column": "part", "operator": "eq", "value": "P2"}]
                    },
                },
                {
                    "id": "r2",
                    "op": "rename_columns",
                    "inputs": ["b2"],
                    "output": "b2r",
                    "params": {"mapping": {"value": "value_p2"}},
                },
                {
                    "id": "j",
                    "op": "join",
                    "inputs": ["b1r", "b2r"],
                    "output": "joined",
                    "params": {
                        "left_keys": ["entity_id"],
                        "right_keys": ["entity_id"],
                        "how": "inner",
                    },
                },
            ],
        }
    )
    return frames, _und_one(src, df), plan


def _d01_reference_plan() -> tuple[dict[str, pd.DataFrame], dict, object]:
    """Exact Phase 39T D01 32B reconstructed shape (generic source name)."""
    src = "well_readings.xlsx"
    df = pd.DataFrame(
        {
            "well_id": ["WL1", "WL1", "WL2", "WL2"],
            "day": ["D1", "D2", "D1", "D2"],
            "liters": [10, 12, 8, 9],
        }
    )
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "joined_wells",
            "reason": "Filtered and renamed D1/D2 liters, then joined on well_id",
            "steps": [
                {
                    "id": "filter_d1",
                    "op": "filter_rows",
                    "inputs": [src],
                    "output": "filtered_d1",
                    "params": {
                        "conditions": [{"column": "day", "operator": "eq", "value": "D1"}]
                    },
                },
                {
                    "id": "rename_d1",
                    "op": "rename_columns",
                    "inputs": ["filtered_d1"],
                    "output": "d1_renamed",
                    "params": {"mapping": {"liters": "liters_D1"}},
                },
                {
                    "id": "filter_d2",
                    "op": "filter_rows",
                    "inputs": [src],
                    "output": "filtered_d2",
                    "params": {
                        "conditions": [{"column": "day", "operator": "eq", "value": "D2"}]
                    },
                },
                {
                    "id": "rename_d2",
                    "op": "rename_columns",
                    "inputs": ["filtered_d2"],
                    "output": "d2_renamed",
                    "params": {"mapping": {"liters": "liters_D2"}},
                },
                {
                    "id": "join_wells",
                    "op": "join",
                    "inputs": ["d1_renamed", "d2_renamed"],
                    "output": "joined_wells",
                    "params": {
                        "left_keys": ["well_id"],
                        "right_keys": ["well_id"],
                        "how": "inner",
                    },
                },
            ],
            "final_output_requirements": {
                "grain": "entity",
                "required_columns": ["well_id", "liters_D1", "liters_D2"],
            },
        }
    )
    return {src: df}, _und_one(src, df), plan


def test_p1_filtered_branch_join_valid_with_frames() -> None:
    frames, und, plan = _d01_like_frames()
    before = {k: v.copy(deep=True) for k, v in frames.items()}
    result = validate_integration_plan(und, plan, frames=frames)
    assert result.valid, [e.code for e in result.errors]
    assert not any(e.code == "many_to_many_join_risk" for e in result.errors)
    assert before["readings.xlsx"].equals(frames["readings.xlsx"])


def test_p1_without_frames_still_uses_source_uniqueness() -> None:
    """Frames omitted: conservative source uniqueness (historical path)."""
    _, und, plan = _d01_like_frames()
    result = validate_integration_plan(und, plan)
    assert any(e.code == "many_to_many_join_risk" for e in result.errors)


def test_p2_filter_leaves_duplicates_still_many_to_many() -> None:
    src = "readings.xlsx"
    df = pd.DataFrame(
        {
            "entity_id": ["A", "A", "A", "A"],
            "part": ["P1", "P1", "P2", "P2"],
            "value": [1, 2, 3, 4],
        }
    )
    frames = {src: df}
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "joined",
            "steps": [
                {
                    "op": "filter_rows",
                    "inputs": [src],
                    "output": "b1",
                    "params": {
                        "conditions": [{"column": "part", "operator": "eq", "value": "P1"}]
                    },
                },
                {
                    "op": "filter_rows",
                    "inputs": [src],
                    "output": "b2",
                    "params": {
                        "conditions": [{"column": "part", "operator": "eq", "value": "P2"}]
                    },
                },
                {
                    "op": "join",
                    "inputs": ["b1", "b2"],
                    "output": "joined",
                    "params": {
                        "left_keys": ["entity_id"],
                        "right_keys": ["entity_id"],
                        "how": "inner",
                    },
                },
            ],
        }
    )
    result = validate_integration_plan(_und_one(src, df), plan, frames=frames)
    assert not result.valid
    assert any(e.code == "many_to_many_join_risk" for e in result.errors)


def test_p3_aggregate_makes_keys_unique_without_frames() -> None:
    src = "readings.xlsx"
    df = pd.DataFrame(
        {
            "entity_id": ["A", "A", "B", "B"],
            "part": ["P1", "P1", "P2", "P2"],
            "value": [1, 2, 3, 4],
        }
    )
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "joined",
            "steps": [
                {
                    "op": "aggregate",
                    "inputs": [src],
                    "output": "a1",
                    "params": {
                        "group_by": ["entity_id"],
                        "metrics": [{"column": "value", "function": "sum", "alias": "v1"}],
                    },
                },
                {
                    "op": "aggregate",
                    "inputs": [src],
                    "output": "a2",
                    "params": {
                        "group_by": ["entity_id"],
                        "metrics": [{"column": "value", "function": "sum", "alias": "v2"}],
                    },
                },
                {
                    "op": "join",
                    "inputs": ["a1", "a2"],
                    "output": "joined",
                    "params": {
                        "left_keys": ["entity_id"],
                        "right_keys": ["entity_id"],
                        "how": "inner",
                    },
                },
            ],
        }
    )
    result = validate_integration_plan(_und_one(src, df), plan)
    assert result.valid, [e.code for e in result.errors]


def test_p4_different_source_one_to_one() -> None:
    a = pd.DataFrame({"k": ["1", "2"], "x": [1, 2]})
    b = pd.DataFrame({"k": ["1", "2"], "y": [3, 4]})
    und = _und_two("L", a, "R", b)
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "j",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["L", "R"],
                    "output": "j",
                    "params": {"left_keys": ["k"], "right_keys": ["k"], "how": "inner"},
                }
            ],
        }
    )
    result = validate_integration_plan(und, plan, frames={"L": a, "R": b})
    assert result.valid, [e.code for e in result.errors]


def test_p5_different_source_many_to_many() -> None:
    a = pd.DataFrame({"k": ["1", "1", "2", "2"], "x": [1, 2, 3, 4]})
    b = pd.DataFrame({"k": ["1", "1", "2", "2"], "y": [5, 6, 7, 8]})
    und = _und_two("L", a, "R", b)
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "j",
            "steps": [
                {
                    "op": "join",
                    "inputs": ["L", "R"],
                    "output": "j",
                    "params": {"left_keys": ["k"], "right_keys": ["k"], "how": "inner"},
                }
            ],
        }
    )
    result = validate_integration_plan(und, plan, frames={"L": a, "R": b})
    assert any(e.code == "many_to_many_join_risk" for e in result.errors)


def test_p6_fake_dual_grain_still_rejected() -> None:
    a = pd.DataFrame({"id": [1, 1], "kg": [4, 5]})
    b = pd.DataFrame({"id": [1], "wing": ["N"]})
    und = _und_two("barn", a, "stalls", b, rel="join_candidate")
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "agg",
            "final_output_requirements": {
                "grain": "entity",
                "required_columns": ["id", "alpha", "beta"],
                "output_roles": [
                    {"role": "entity_key", "columns": ["id"]},
                    {"role": "comparison_side", "columns": ["alpha"], "side_id": "A"},
                    {"role": "comparison_side", "columns": ["beta"], "side_id": "B"},
                ],
            },
            "steps": [
                {
                    "op": "join",
                    "inputs": ["barn", "stalls"],
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
                            {"column": "kg", "function": "sum", "alias": "alpha"},
                            {"column": "kg", "function": "sum", "alias": "beta"},
                        ],
                    },
                },
            ],
        }
    )
    without = validate_integration_plan(und, plan)
    with_frames = validate_integration_plan(und, plan, frames={"barn": a, "stalls": b})
    assert not without.valid and not with_frames.valid
    assert any(e.code == "final_grain_contradiction" for e in without.errors)
    assert any(e.code == "final_grain_contradiction" for e in with_frames.errors)


def test_one_side_unique_is_not_many_to_many() -> None:
    src = "readings.xlsx"
    df = pd.DataFrame(
        {
            "entity_id": ["A", "A", "B", "B", "A"],
            "part": ["P1", "P2", "P1", "P2", "P2"],
            "value": [1, 2, 3, 4, 5],
        }
    )
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "joined",
            "steps": [
                {
                    "op": "filter_rows",
                    "inputs": [src],
                    "output": "b1",
                    "params": {
                        "conditions": [{"column": "part", "operator": "eq", "value": "P1"}]
                    },
                },
                {
                    "op": "filter_rows",
                    "inputs": [src],
                    "output": "b2",
                    "params": {
                        "conditions": [{"column": "part", "operator": "eq", "value": "P2"}]
                    },
                },
                {
                    "op": "join",
                    "inputs": ["b1", "b2"],
                    "output": "joined",
                    "params": {
                        "left_keys": ["entity_id"],
                        "right_keys": ["entity_id"],
                        "how": "inner",
                    },
                },
            ],
        }
    )
    result = validate_integration_plan(_und_one(src, df), plan, frames={src: df})
    assert not any(e.code == "many_to_many_join_risk" for e in result.errors)


def test_composite_key_uniqueness_after_filter() -> None:
    src = "readings.xlsx"
    df = pd.DataFrame(
        {
            "entity_id": ["A", "A", "A", "A"],
            "period": ["Q1", "Q1", "Q2", "Q2"],
            "part": ["P1", "P2", "P1", "P2"],
            "value": [1, 2, 3, 4],
        }
    )
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "joined",
            "steps": [
                {
                    "op": "filter_rows",
                    "inputs": [src],
                    "output": "b1",
                    "params": {
                        "conditions": [{"column": "part", "operator": "eq", "value": "P1"}]
                    },
                },
                {
                    "op": "filter_rows",
                    "inputs": [src],
                    "output": "b2",
                    "params": {
                        "conditions": [{"column": "part", "operator": "eq", "value": "P2"}]
                    },
                },
                {
                    "op": "join",
                    "inputs": ["b1", "b2"],
                    "output": "joined",
                    "params": {
                        "left_keys": ["entity_id", "period"],
                        "right_keys": ["entity_id", "period"],
                        "how": "inner",
                    },
                },
            ],
        }
    )
    result = validate_integration_plan(_und_one(src, df), plan, frames={src: df})
    assert result.valid, [e.code for e in result.errors]


def test_select_before_join_preserves_cardinality() -> None:
    frames, und, plan = _d01_like_frames()
    steps = list(plan.to_dict()["steps"])
    steps.insert(
        4,
        {
            "id": "sel",
            "op": "select_columns",
            "inputs": ["b1r"],
            "output": "b1s",
            "params": {"columns": ["entity_id", "value_p1"]},
        },
    )
    steps[-1]["inputs"] = ["b1s", "b2r"]
    plan2 = integration_plan_from_dict({**plan.to_dict(), "steps": steps})
    result = validate_integration_plan(und, plan2, frames=frames)
    assert result.valid, [e.code for e in result.errors]


def test_irrelevant_filter_does_not_bypass() -> None:
    src = "readings.xlsx"
    df = pd.DataFrame(
        {
            "entity_id": ["A", "A", "B", "B"],
            "flag": ["keep", "keep", "keep", "keep"],
            "value": [1, 2, 3, 4],
        }
    )
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "joined",
            "steps": [
                {
                    "op": "filter_rows",
                    "inputs": [src],
                    "output": "b1",
                    "params": {
                        "conditions": [{"column": "flag", "operator": "eq", "value": "keep"}]
                    },
                },
                {
                    "op": "filter_rows",
                    "inputs": [src],
                    "output": "b2",
                    "params": {
                        "conditions": [{"column": "flag", "operator": "eq", "value": "keep"}]
                    },
                },
                {
                    "op": "join",
                    "inputs": ["b1", "b2"],
                    "output": "joined",
                    "params": {
                        "left_keys": ["entity_id"],
                        "right_keys": ["entity_id"],
                        "how": "inner",
                    },
                },
            ],
        }
    )
    result = validate_integration_plan(_und_one(src, df), plan, frames={src: df})
    assert any(e.code == "many_to_many_join_risk" for e in result.errors)


def test_validator_does_not_mutate_plan() -> None:
    frames, und, plan = _d01_like_frames()
    before = copy.deepcopy(plan.to_dict())
    validate_integration_plan(und, plan, frames=frames)
    assert plan.to_dict() == before


def test_d01_reference_valid_and_executes() -> None:
    frames, und, plan = _d01_reference_plan()
    val = validate_integration_plan(und, plan, frames=frames)
    assert val.valid, [e.code for e in val.errors]
    exe = execute_integration_plan(frames, plan, val)
    assert exe.success, exe.error
    assert exe.final_output is not None
    assert int(len(exe.final_output)) == 2
    assert "liters_D1" in exe.final_output.columns
    assert "liters_D2" in exe.final_output.columns
    assert frames["well_readings.xlsx"].equals(
        pd.DataFrame(
            {
                "well_id": ["WL1", "WL1", "WL2", "WL2"],
                "day": ["D1", "D2", "D1", "D2"],
                "liters": [10, 12, 8, 9],
            }
        )
    )


def test_filter_then_aggregate_then_join() -> None:
    src = "readings.xlsx"
    df = pd.DataFrame(
        {
            "entity_id": ["A", "A", "B", "B"],
            "part": ["P1", "P1", "P2", "P2"],
            "value": [1, 2, 3, 4],
        }
    )
    plan = integration_plan_from_dict(
        {
            "status": "planned",
            "final_output": "joined",
            "steps": [
                {
                    "op": "filter_rows",
                    "inputs": [src],
                    "output": "f1",
                    "params": {
                        "conditions": [{"column": "part", "operator": "eq", "value": "P1"}]
                    },
                },
                {
                    "op": "aggregate",
                    "inputs": ["f1"],
                    "output": "a1",
                    "params": {
                        "group_by": ["entity_id"],
                        "metrics": [{"column": "value", "function": "sum", "alias": "v1"}],
                    },
                },
                {
                    "op": "filter_rows",
                    "inputs": [src],
                    "output": "f2",
                    "params": {
                        "conditions": [{"column": "part", "operator": "eq", "value": "P2"}]
                    },
                },
                {
                    "op": "aggregate",
                    "inputs": ["f2"],
                    "output": "a2",
                    "params": {
                        "group_by": ["entity_id"],
                        "metrics": [{"column": "value", "function": "sum", "alias": "v2"}],
                    },
                },
                {
                    "op": "join",
                    "inputs": ["a1", "a2"],
                    "output": "joined",
                    "params": {
                        "left_keys": ["entity_id"],
                        "right_keys": ["entity_id"],
                        "how": "inner",
                    },
                },
            ],
        }
    )
    result = validate_integration_plan(_und_one(src, df), plan, frames={src: df})
    assert result.valid, [e.code for e in result.errors]
