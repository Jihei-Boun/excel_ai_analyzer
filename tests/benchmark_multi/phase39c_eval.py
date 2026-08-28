"""Phase 39C evaluation harness (measure-only; no Phase 39B logic changes)."""

from __future__ import annotations

import argparse
import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.integration_planner import build_integration_plan
from core.integrate.relationship_infer import build_cross_file_understanding
from core.integrate.semantic_escalation import (
    SemanticEscalationConfig,
    run_integration_pipeline_semantic_experimental,
)
from core.integrate.semantic_verifier import run_semantic_verification
from tests.benchmark_multi.phase34_generalization import (
    load_canonical_historical_fixture,
)

ROOT = Path(__file__).resolve().parents[2]
FIX39B = ROOT / "tests/benchmark_multi/fixtures/phase39b/c2_and_controls.json"
FIX39C = ROOT / "tests/benchmark_multi/fixtures/phase39c/offline_generalization.json"
OUT = ROOT / "benchmark_results/multi/phase39c"

PLANNER_MODEL = "qwen2.5:7b"
VERIFIER_MODEL = "qwen2.5:7b"
STRONG_MODEL = "qwen3:32b"  # local Ollama tag (config default qwen3:32b unavailable)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _strip_roles(plan: dict[str, Any]) -> dict[str, Any]:
    p = deepcopy(plan)
    req = p.get("final_output_requirements")
    if isinstance(req, dict) and "output_roles" in req:
        req = dict(req)
        req.pop("output_roles", None)
        p["final_output_requirements"] = req
    return p


def _verify(
    prompt: str,
    plan: dict[str, Any],
    *,
    strip_roles: bool,
) -> dict[str, Any]:
    plan_in = _strip_roles(plan) if strip_roles else plan
    t0 = time.time()
    r = run_semantic_verification(
        user_prompt=prompt,
        plan=plan_in,
        variant="V1",
        model=VERIFIER_MODEL,
        independent=True,
    )
    return {
        "verdict": r.verdict,
        "reason_code": r.reason_code,
        "evidence": list(r.evidence or []),
        "elapsed_s": round(time.time() - t0, 3),
        "strip_roles": strip_roles,
    }


def _score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    c2 = [r for r in rows if r["family"] == "c2_wrong"]
    vc = [r for r in rows if r["family"] == "valid_control"]
    c2_hit = sum(1 for r in c2 if r["verdict"] != "pass")
    vc_fp = sum(1 for r in vc if r["verdict"] == "fail")
    vc_non = sum(1 for r in vc if r["verdict"] != "pass")
    return {
        "n_c2": len(c2),
        "n_valid": len(vc),
        "c2_non_pass": c2_hit,
        "c2_recall_non_pass": round(c2_hit / len(c2), 3) if c2 else None,
        "valid_fail_fp": vc_fp,
        "valid_fail_fp_rate": round(vc_fp / len(vc), 3) if vc else None,
        "valid_non_pass": vc_non,
        "uncertain": sum(1 for r in rows if r["verdict"] == "uncertain"),
        "mean_elapsed_s": round(
            sum(float(r["elapsed_s"]) for r in rows) / max(len(rows), 1), 3
        ),
    }


def run_offline() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for c in _load(FIX39B)["cases"]:
        cases.append(
            {
                "id": f"P39B-{c['id']}",
                "source": "phase39b",
                "family": c["family"],
                "prompt": c["prompt"],
                "plan": c["plan"],
            }
        )
    for c in _load(FIX39C)["cases"]:
        cases.append(
            {
                "id": c["id"],
                "source": "phase39c",
                "family": c["family"],
                "prompt": c["prompt"],
                "plan": c["plan"],
                "pattern": c.get("pattern"),
                "domain": c.get("domain"),
            }
        )

    rows_b: list[dict[str, Any]] = []
    rows_c: list[dict[str, Any]] = []
    for c in cases:
        vb = _verify(c["prompt"], c["plan"], strip_roles=True)
        rows_b.append({**c, **vb, "variant": "B"})
        vc = _verify(c["prompt"], c["plan"], strip_roles=False)
        rows_c.append({**c, **vc, "variant": "C"})

    # Historical Type C1 — unique prompts only
    _valid, type_c = load_canonical_historical_fixture()
    hist: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in type_c:
        prompt = str(item.get("user_prompt") or "")
        if prompt in seen:
            continue
        seen.add(prompt)
        plan = item.get("plan") or {}
        vr = _verify(prompt, plan, strip_roles=False)
        hist.append(
            {
                "id": item.get("dataset_id") or item.get("case_id_analysis_only"),
                "family": "historical_c1",
                "prompt": prompt,
                "label": item.get("label"),
                **vr,
            }
        )

    report = {
        "tier": 1,
        "variant_B_roles_stripped": {
            "all": _score(rows_b),
            "phase39c_only": _score([r for r in rows_b if r["source"] == "phase39c"]),
            "rows": rows_b,
        },
        "variant_C_roles_kept": {
            "all": _score(rows_c),
            "phase39c_only": _score([r for r in rows_c if r["source"] == "phase39c"]),
            "rows": rows_c,
        },
        "historical_c1": {
            "n": len(hist),
            "non_pass": sum(1 for r in hist if r["verdict"] != "pass"),
            "rows": hist,
        },
        "false_passes_C": [
            r for r in rows_c if r["family"] == "c2_wrong" and r["verdict"] == "pass"
        ],
        "false_fails_C": [
            r
            for r in rows_c
            if r["family"] == "valid_control" and r["verdict"] in {"fail", "uncertain"}
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tier1_offline.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def _frames(kind: str) -> dict[str, pd.DataFrame]:
    if kind == "energy":
        return {
            "p1_usage.xlsx": pd.DataFrame({"site_id": ["S1", "S2"], "kwh": [10, 20]}),
            "p2_usage.xlsx": pd.DataFrame({"site_id": ["S1", "S2"], "kwh": [12, 18]}),
        }
    if kind == "finance":
        return {
            "actuals.xlsx": pd.DataFrame(
                {"cost_center": ["C1", "C2"], "amount": [100, 200]}
            ),
            "budget.xlsx": pd.DataFrame(
                {"cost_center": ["C1", "C2"], "amount": [90, 220]}
            ),
        }
    if kind == "saas":
        return {
            "snapshot_old.xlsx": pd.DataFrame(
                {"account_id": ["A1", "A2"], "active_users": [5, 8]}
            ),
            "snapshot_new.xlsx": pd.DataFrame(
                {"account_id": ["A1", "A2"], "active_users": [7, 6]}
            ),
        }
    if kind == "ml":
        return {
            "baseline_metrics.xlsx": pd.DataFrame(
                {"model_id": ["m1", "m2"], "score": [0.7, 0.8]}
            ),
            "experiment_metrics.xlsx": pd.DataFrame(
                {"model_id": ["m1", "m2"], "score": [0.75, 0.7]}
            ),
        }
    if kind == "append":
        return {
            "t1.xlsx": pd.DataFrame({"id": [1, 2], "v": [1, 2]}),
            "t2.xlsx": pd.DataFrame({"id": [3, 4], "v": [3, 4]}),
        }
    raise ValueError(kind)


LIVE_CASES = [
    {
        "id": "LIVE-C2-energy",
        "roles_required": True,
        "kind": "energy",
        "prompt": "Compare site electricity use for period P1 versus period P2 and keep both period totals visible by site_id.",
    },
    {
        "id": "LIVE-C2-finance",
        "roles_required": True,
        "kind": "finance",
        "prompt": "Show actual spend and budgeted spend side by side for each cost_center.",
    },
    {
        "id": "LIVE-C2-saas",
        "roles_required": True,
        "kind": "saas",
        "prompt": "Which accounts increased active_users from snapshot_old to snapshot_new? Keep both snapshots observable.",
    },
    {
        "id": "LIVE-C2-ml",
        "roles_required": True,
        "kind": "ml",
        "prompt": "Contrast baseline metrics with experiment metrics per model_id; I need both sides visible.",
    },
    {
        "id": "LIVE-C2-change",
        "roles_required": True,
        "kind": "energy",
        "prompt": "Which site changed the most between p1_usage and p2_usage kwh? Preserve both period values.",
    },
    {
        "id": "LIVE-VC-combine-total",
        "roles_required": False,
        "kind": "energy",
        "prompt": "Combine both usage files and show total kwh by site_id.",
    },
    {
        "id": "LIVE-VC-overall",
        "roles_required": False,
        "kind": "finance",
        "prompt": "What is the overall amount total across both files?",
    },
    {
        "id": "LIVE-VC-append",
        "roles_required": False,
        "kind": "append",
        "prompt": "Append the two tables into one detail table.",
    },
    {
        "id": "LIVE-VC-integrated-total",
        "roles_required": False,
        "kind": "finance",
        "prompt": "Integrate the two ledgers and give amount totals by cost_center; no need to keep file identity.",
    },
    {
        "id": "LIVE-VC-combined-mean",
        "roles_required": False,
        "kind": "ml",
        "prompt": "Combine baseline and experiment score rows and report mean score by model_id across all runs.",
    },
]


def _role_stats(plan: dict[str, Any] | None) -> dict[str, Any]:
    if not plan:
        return {
            "has_roles": False,
            "n_comparison_sides": 0,
            "side_ids": [],
            "distinct_side_ids": 0,
            "roles": [],
        }
    req = plan.get("final_output_requirements") or {}
    roles = req.get("output_roles") or []
    sides = [
        r
        for r in roles
        if isinstance(r, dict) and r.get("role") == "comparison_side"
    ]
    side_ids = sorted({str(r.get("side_id")) for r in sides if r.get("side_id")})
    return {
        "has_roles": bool(roles),
        "n_roles": len(roles),
        "n_comparison_sides": len(sides),
        "side_ids": side_ids,
        "distinct_side_ids": len(side_ids),
        "roles": roles,
    }


def _classify(plan: dict[str, Any] | None, *, roles_required: bool) -> str:
    if not plan or plan.get("status") != "planned":
        return "cannot_plan_or_empty"
    ops = [s.get("op") for s in (plan.get("steps") or []) if isinstance(s, dict)]
    n_agg = sum(1 for o in ops if o == "aggregate")
    has_union = "union_rows" in ops
    has_join = "join" in ops
    if roles_required:
        if n_agg >= 2 and has_join:
            return "correct_dual_side"
        if has_union and n_agg == 1 and not has_join:
            return "wrong_collapse"
        return "other_wrong_or_partial"
    if ops == ["union_rows"]:
        return "correct_append"
    if has_union and n_agg >= 1:
        return "correct_combined_agg"
    return "other"


def run_live(repeats: int = 1) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in LIVE_CASES:
        sources = _frames(case["kind"])
        und = build_cross_file_understanding(
            list(sources.items()),
            model=PLANNER_MODEL,
            infer_relationships=True,
        )
        for rep in range(repeats):
            t0 = time.time()
            err = None
            plan_d: dict[str, Any] | None
            try:
                plan = build_integration_plan(
                    case["prompt"], und, model=PLANNER_MODEL
                )
                plan_d = plan.to_dict()
            except Exception as exc:  # noqa: BLE001
                plan_d = None
                err = f"{type(exc).__name__}: {exc}"
            elapsed = round(time.time() - t0, 3)
            roles = _role_stats(plan_d)
            need = bool(case["roles_required"])
            declared_ok = (
                roles["distinct_side_ids"] >= 2 and roles["n_comparison_sides"] >= 2
            )
            if plan_d and plan_d.get("status") == "planned":
                ver_b = _verify(case["prompt"], plan_d, strip_roles=True)
                ver_c = _verify(case["prompt"], plan_d, strip_roles=False)
            else:
                ver_b = ver_c = {"verdict": None}
            rows.append(
                {
                    "id": case["id"],
                    "rep": rep,
                    "roles_required": need,
                    "prompt": case["prompt"],
                    "plan_status": (plan_d or {}).get("status"),
                    "ops": [
                        s.get("op")
                        for s in ((plan_d or {}).get("steps") or [])
                        if isinstance(s, dict)
                    ],
                    "composition": _classify(plan_d, roles_required=need),
                    "roles": roles,
                    "role_declared_ok": declared_ok,
                    "role_under": need and not declared_ok,
                    "role_over": (not need) and roles["n_comparison_sides"] > 0,
                    "planner_elapsed_s": elapsed,
                    "planner_error": err,
                    "verifier_B": ver_b,
                    "verifier_C": ver_c,
                    "plan": plan_d,
                }
            )

    req = [r for r in rows if r["roles_required"]]
    non = [r for r in rows if not r["roles_required"]]
    declared = [r for r in rows if r["roles"]["n_comparison_sides"] > 0]
    summary = {
        "n_required": len(req),
        "n_not_required": len(non),
        "role_recall": round(
            sum(1 for r in req if r["role_declared_ok"]) / len(req), 3
        )
        if req
        else None,
        "role_precision": round(
            sum(1 for r in declared if r["roles_required"] and r["role_declared_ok"])
            / len(declared),
            3,
        )
        if declared
        else None,
        "overdeclaration_rate": round(
            sum(1 for r in non if r["role_over"]) / len(non), 3
        )
        if non
        else None,
        "composition_required": {
            k: sum(1 for r in req if r["composition"] == k)
            for k in sorted({r["composition"] for r in req})
        },
        "composition_not_required": {
            k: sum(1 for r in non if r["composition"] == k)
            for k in sorted({r["composition"] for r in non})
        },
    }
    report = {"tier": 2, "summary": summary, "rows": rows}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tier2_live_planner.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


E2E_CASES = [LIVE_CASES[0], LIVE_CASES[1], LIVE_CASES[2], LIVE_CASES[5]]


def run_e2e() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    cfg = SemanticEscalationConfig(
        enable_failure_escalation=True,
        enable_semantic_escalation=True,
        uncertain_policy="escalate",
        verifier_model=VERIFIER_MODEL,
        strong_model=STRONG_MODEL,
        strong_max_retries=2,
        reverify_strong=True,
    )
    for case in E2E_CASES:
        sources = _frames(case["kind"])
        und = build_cross_file_understanding(
            list(sources.items()),
            model=PLANNER_MODEL,
            infer_relationships=True,
        )
        t0 = time.time()
        err = None
        result = None
        try:
            result = run_integration_pipeline_semantic_experimental(
                case["prompt"],
                sources,
                und,
                config=cfg,
            )
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
        elapsed = round(time.time() - t0, 3)
        meta = dict(getattr(result, "metadata", None) or {}) if result else {}
        plan = result.plan.to_dict() if result and result.plan else None
        sem = meta.get("semantic_verifier") or {}
        esc = meta.get("semantic_escalation") or {}
        rows.append(
            {
                "id": case["id"],
                "roles_required": case["roles_required"],
                "prompt": case["prompt"],
                "pipeline_status": getattr(result, "status", None),
                "elapsed_s": elapsed,
                "error": err,
                "plan": plan,
                "ops": [
                    s.get("op")
                    for s in ((plan or {}).get("steps") or [])
                    if isinstance(s, dict)
                ],
                "roles": _role_stats(plan),
                "composition": _classify(
                    plan, roles_required=bool(case["roles_required"])
                ),
                "semantic_verifier_verdict": (sem.get("verdict") if isinstance(sem, dict) else None),
                "semantic_verifier": sem,
                "semantic_escalation": esc,
                "failure_escalation_32b": meta.get("failure_escalation_32b"),
                "semantic_escalation_32b": meta.get("semantic_escalation_32b"),
                "final_path": meta.get("final_path"),
                "metadata_keys": sorted(meta.keys()),
            }
        )
    report = {"tier": 3, "strong_model": STRONG_MODEL, "rows": rows}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tier3_e2e_escalation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["1", "2", "3", "all"], default="all")
    ap.add_argument("--live-repeats", type=int, default=1)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.tier in {"1", "all"}:
        r1 = run_offline()
        print("TIER1 B39c", r1["variant_B_roles_stripped"]["phase39c_only"])
        print("TIER1 C39c", r1["variant_C_roles_kept"]["phase39c_only"])
        print("TIER1 Ball", r1["variant_B_roles_stripped"]["all"])
        print("TIER1 Call", r1["variant_C_roles_kept"]["all"])
        print(
            "TIER1 hist_c1",
            r1["historical_c1"]["non_pass"],
            "/",
            r1["historical_c1"]["n"],
        )
        print("FP", len(r1["false_passes_C"]), "FF", len(r1["false_fails_C"]))
    if args.tier in {"2", "all"}:
        r2 = run_live(repeats=args.live_repeats)
        print("TIER2", json.dumps(r2["summary"], indent=2))
    if args.tier in {"3", "all"}:
        r3 = run_e2e()
        for row in r3["rows"]:
            print(
                "TIER3",
                row["id"],
                row["pipeline_status"],
                row["composition"],
                "sem32b=",
                row.get("semantic_escalation_32b"),
                "t=",
                row["elapsed_s"],
                "err=",
                row.get("error"),
            )


if __name__ == "__main__":
    main()
