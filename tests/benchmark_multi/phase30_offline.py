"""Phase 30 offline: blocking grain consistency vs frozen plans + stress fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.relationship_infer import build_cross_file_understanding
from tests.benchmark_multi import DATASETS_DIR
from tests.benchmark_multi.generate_datasets import ensure_datasets
from tests.benchmark_multi.schema import load_all_cases

OUT = Path("benchmark_results/multi/phase30")


def _is_silent(c: dict[str, Any]) -> bool:
    return (
        c.get("status") == "success"
        and not bool(c.get("overall_ok"))
        and not bool(c.get("unsafe_execution"))
    )


def _is_valid(c: dict[str, Any]) -> bool:
    return c.get("status") == "success" and bool(c.get("overall_ok"))


def _load_frozen_successes() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for root in (
        Path("benchmark_results/multi/phase27/qwen2.5_7b/full_19"),
        Path("benchmark_results/multi/phase28/live_escalation"),
    ):
        for p in sorted(root.glob("2026*.json")):
            for c in json.loads(p.read_text(encoding="utf-8")).get("cases") or []:
                if c.get("status") == "success" and c.get("plan"):
                    cases.append(c)
    return cases


def _understanding_for_case(case_id: str) -> dict[str, Any] | None:
    ensure_datasets(DATASETS_DIR, force=False)
    case = next((c for c in load_all_cases() if c.id == case_id), None)
    if case is None:
        return None
    sources = {
        Path(f).stem: pd.read_excel(DATASETS_DIR / f) for f in case.files
    }
    und = build_cross_file_understanding(
        list(sources.items()), infer_relationships=False
    ).to_dict()
    if case.fixed_relationships:
        und["relationships"] = list(case.fixed_relationships)
    return und


def _blocked_by_grain(plan_dict: dict[str, Any], und: dict[str, Any]) -> bool:
    plan = integration_plan_from_dict(plan_dict)
    val = validate_integration_plan(und, plan)
    return any(e.code == "final_grain_contradiction" for e in val.errors)


def experiment_b_offline() -> dict[str, Any]:
    frozen = _load_frozen_successes()
    und_cache: dict[str, dict[str, Any]] = {}
    TP = FP = TN = FN = 0
    details: list[dict[str, Any]] = []
    for c in frozen:
        cid = str(c.get("case_id"))
        if cid not in und_cache:
            und = _understanding_for_case(cid)
            if und is None:
                continue
            und_cache[cid] = und
        und = und_cache[cid]
        try:
            blocked = _blocked_by_grain(c.get("plan") or {}, und)
        except Exception as exc:  # noqa: BLE001
            details.append({"case_id": cid, "error": str(exc)})
            continue
        silent = _is_silent(c)
        valid = _is_valid(c)
        if silent and blocked:
            TP += 1
        elif silent and not blocked:
            FN += 1
        elif valid and blocked:
            FP += 1
        elif valid and not blocked:
            TN += 1
        if blocked or silent:
            details.append(
                {
                    "case_id": cid,
                    "silent": silent,
                    "valid": valid,
                    "blocked": blocked,
                    "ops": c.get("selected_operations"),
                    "grain": ((c.get("plan") or {}).get("final_output_requirements") or {}).get(
                        "grain"
                    ),
                }
            )
    return {
        "TP": TP,
        "FP": FP,
        "TN": TN,
        "FN": FN,
        "precision": round(TP / (TP + FP), 3) if TP + FP else None,
        "recall": round(TP / (TP + FN), 3) if TP + FN else None,
        "fpr": round(FP / (FP + TN), 3) if FP + TN else None,
        "details": details,
    }


def stress_fixtures() -> dict[str, Any]:
    """Generic valid aggregate plans that must remain accepted."""
    results = []

    def check(name: str, und: dict[str, Any], plan: dict[str, Any], expect_valid: bool) -> None:
        val = validate_integration_plan(und, integration_plan_from_dict(plan))
        grain_err = [e for e in val.errors if e.code == "final_grain_contradiction"]
        ok = val.valid if expect_valid else (not val.valid and bool(grain_err))
        results.append(
            {
                "name": name,
                "expect_valid": expect_valid,
                "valid": val.valid,
                "grain_errors": [e.message for e in grain_err],
                "pass": ok,
            }
        )

    a = pd.DataFrame({"id": [1, 1], "g": ["x", "y"], "amt": [1, 2]})
    b = pd.DataFrame({"id": [1], "name": ["n"]})
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

    # valid: join → aggregate with group grain
    check(
        "join_aggregate_group",
        und,
        {
            "status": "planned",
            "final_output": "agg",
            "final_output_requirements": {"grain": "group", "required_columns": ["id", "s"]},
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
                        "metrics": [{"column": "amt", "function": "sum", "alias": "s"}],
                    },
                },
            ],
        },
        True,
    )

    # valid: multi group keys + mean/sum + select
    check(
        "multi_key_multi_metric_select",
        und,
        {
            "status": "planned",
            "final_output": "out",
            "final_output_requirements": {
                "grain": "summary",
                "required_columns": ["id", "g", "s", "m"],
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
                        "metrics": [
                            {"column": "amt", "function": "sum", "alias": "s"},
                            {"column": "amt", "function": "mean", "alias": "m"},
                        ],
                    },
                },
                {
                    "op": "select_columns",
                    "inputs": ["agg"],
                    "output": "out",
                    "params": {"columns": ["id", "g", "s", "m"]},
                },
            ],
        },
        True,
    )

    # valid: union → aggregate
    u1 = pd.DataFrame({"id": [1], "x": [1]})
    u2 = pd.DataFrame({"id": [2], "x": [2]})
    und_u = build_cross_file_understanding(
        [("A", u1), ("B", u2)], infer_relationships=False
    ).to_dict()
    check(
        "union_aggregate_group",
        und_u,
        {
            "status": "planned",
            "final_output": "agg",
            "final_output_requirements": {"grain": "group", "required_columns": ["id", "sx"]},
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
        },
        True,
    )

    # valid: rename → aggregate
    r = pd.DataFrame({"old": [1, 1], "x": [1, 2]})
    und_r = build_cross_file_understanding([("T", r)], infer_relationships=False).to_dict()
    check(
        "rename_aggregate_group",
        und_r,
        {
            "status": "planned",
            "final_output": "agg",
            "final_output_requirements": {"grain": "group", "required_columns": ["id", "sx"]},
            "steps": [
                {
                    "op": "rename_columns",
                    "inputs": ["T"],
                    "output": "ren",
                    "params": {"mapping": {"old": "id"}},
                },
                {
                    "op": "aggregate",
                    "inputs": ["ren"],
                    "output": "agg",
                    "params": {
                        "group_by": ["id"],
                        "metrics": [{"column": "x", "function": "sum", "alias": "sx"}],
                    },
                },
            ],
        },
        True,
    )

    # invalid Type-D shape: entity + collapsing aggregate
    check(
        "entity_plus_collapse_blocked",
        und,
        {
            "status": "planned",
            "final_output": "agg",
            "final_output_requirements": {"grain": "entity", "required_columns": ["id", "s"]},
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
                        "metrics": [{"column": "amt", "function": "sum", "alias": "s"}],
                    },
                },
            ],
        },
        False,
    )

    n_fp = sum(1 for r in results if r["expect_valid"] and not r["pass"])
    n_miss = sum(1 for r in results if (not r["expect_valid"]) and not r["pass"])
    return {
        "cases": results,
        "all_pass": all(r["pass"] for r in results),
        "false_positives": n_fp,
        "missed_blocks": n_miss,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    diag_baseline = {
        "experiment": "A_diagnostic_baseline_phase29",
        "TP": 8,
        "FP": 0,
        "TN": 60,
        "FN": 8,
        "note": "Phase 29 row_grain_with_collapsing_aggregate on frozen success corpus",
    }
    offline = experiment_b_offline()
    stress = stress_fixtures()
    (OUT / "grain_candidate_offline.json").write_text(
        json.dumps(
            {"experiment_A": diag_baseline, "experiment_B_blocking_offline": offline},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (OUT / "grain_stress_test.json").write_text(
        json.dumps({"experiment_C": stress}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("A", diag_baseline)
    print("B", {k: offline[k] for k in ("TP", "FP", "TN", "FN", "fpr", "precision", "recall")})
    print("C", {"all_pass": stress["all_pass"], "fp": stress["false_positives"]})


if __name__ == "__main__":
    main()
