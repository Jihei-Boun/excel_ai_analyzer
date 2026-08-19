"""Phase 28 offline escalation simulation using Phase 27 frozen 7B/32B results.

Does NOT call LLMs. Production decisions are never based on golden — simulation
uses 32B frozen outcomes only for expected recovery / FP-FN analysis.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from core.integrate.planner_model_strategy import (
    PlannerModelStrategy,
    should_escalate_after_fast_path,
)

PHASE27 = Path("benchmark_results/multi/phase27")
PHASE28 = Path("benchmark_results/multi/phase28")

# Phase 27 measured planner call means (seconds)
LAT_7B = 9.616
LAT_32B = 139.811
# Approximate suite wall per case proxies (full suite wall / 19)
WALL_7B_PER_CASE = 500.97 / 19
WALL_32B_PER_CASE = 6851.62 / 19


def _load_run(slug: str, run: int) -> dict[str, Any]:
    return json.loads((PHASE27 / slug / "full_19" / f"run{run}.json").read_text(encoding="utf-8"))


def _meta_from_case(c: dict[str, Any]) -> dict[str, Any]:
    """Rebuild pipeline-like metadata from benchmark case for policy."""
    meta = dict(c.get("metadata") or {})
    obs = c.get("observability") or {}
    levels = (c.get("levels") or {}).get("L6_recovery") or {}
    # Prefer pipeline metadata; fall back to L6 / observability
    out = {
        "exhausted": c.get("status") == "failed",
        "plan_validation_failure_count": levels.get("plan_validation_failure_count")
        or meta.get("plan_validation_failure_count")
        or 0,
        "execution_failure_count": levels.get("execution_failure_count")
        or meta.get("execution_failure_count")
        or 0,
        "result_validation_failure_count": levels.get("result_validation_failure_count")
        or meta.get("result_validation_failure_count")
        or 0,
        "duplicate_plan_count": levels.get("duplicate_plan_count")
        or meta.get("duplicate_plan_count")
        or 0,
        "same_family_repeat_count": levels.get("same_family_repeat_count")
        or meta.get("same_family_repeat_count")
        or 0,
        "repeated_final_contract_failure": meta.get("repeated_final_contract_failure")
        or obs.get("repeated_final_contract_failure")
        or False,
        "validator_blocked_unsafe_plan": meta.get("validator_blocked_unsafe_plan")
        or obs.get("validator_blocked_unsafe_plan")
        or False,
    }
    # Count validation failures from retry_log if counters missing
    retry_log = c.get("retry_log") or []
    if not out["plan_validation_failure_count"]:
        out["plan_validation_failure_count"] = sum(
            1
            for e in retry_log
            if isinstance(e, dict) and e.get("failure_stage") == "integration_plan_validation"
        )
    if not out["result_validation_failure_count"]:
        out["result_validation_failure_count"] = sum(
            1
            for e in retry_log
            if isinstance(e, dict) and e.get("failure_stage") == "integration_result_validation"
        )
    if not out["execution_failure_count"]:
        out["execution_failure_count"] = sum(
            1
            for e in retry_log
            if isinstance(e, dict) and e.get("failure_stage") == "integration_execution"
        )
    if not out["duplicate_plan_count"]:
        out["duplicate_plan_count"] = sum(
            1
            for e in retry_log
            if isinstance(e, dict) and "repeated_plan" in (e.get("failure_codes") or [])
        )
    if not out["same_family_repeat_count"]:
        out["same_family_repeat_count"] = sum(
            1
            for e in retry_log
            if isinstance(e, dict)
            and "repeated_integration_family" in (e.get("failure_codes") or [])
        )
    if not out["repeated_final_contract_failure"]:
        # Heuristic from codes in log
        fc = {
            "join_key_dropped_in_final_projection",
            "final_required_field_missing",
            "required_field_permanently_lost",
        }
        hits = 0
        for e in retry_log:
            if not isinstance(e, dict):
                continue
            if e.get("failure_stage") != "integration_plan_validation":
                continue
            if fc.intersection(e.get("failure_codes") or []):
                hits += 1
        out["repeated_final_contract_failure"] = hits >= 2
    return out


def _scenario_rate(
    cases: list[dict[str, Any]],
    _label: str,
    field: str,
    *,
    case_id_substr: str | None = None,
) -> float:
    """Scenario-scoped rate (match Phase 27 KPI denominator)."""
    if case_id_substr:
        subset = [c for c in cases if case_id_substr in str(c.get("case_id"))]
    else:
        subset = [c for c in cases if field in c and c.get(field) is not None]
        if not subset:
            subset = [c for c in cases if "composite_key" in str(c.get("case_id"))]
    if not subset:
        return 0.0
    return round(100.0 * sum(1 for c in subset if c.get(field) is True) / len(subset), 2)


def simulate_run(run_i: int, strategy: PlannerModelStrategy) -> dict[str, Any]:
    r7 = _load_run("qwen2.5_7b", run_i)
    r32 = _load_run("qwen3_32b", run_i)
    m32 = {c["case_id"]: c for c in r32["cases"]}

    cases_out = []
    esc_n = 0
    for c7 in r7["cases"]:
        cid = c7["case_id"]
        c32 = m32[cid]
        decision = should_escalate_after_fast_path(
            status=str(c7.get("status")),
            retry_log=list(c7.get("retry_log") or []),
            metadata=_meta_from_case(c7),
            strategy=strategy,
        )
        if decision.should_escalate:
            esc_n += 1
            chosen = c32
            path = "escalated_to_32b_frozen"
        else:
            chosen = c7
            path = "fast_7b_frozen"

        # FP/FN analysis (benchmark-only; uses golden overall_ok + 32b oracle)
        would_need = (not bool(c7.get("overall_ok"))) and bool(c32.get("overall_ok"))
        false_positive = decision.should_escalate and bool(c7.get("overall_ok"))
        # Also FP if legitimate cannot_plan success and we escalate
        if decision.should_escalate and c7.get("status") == "cannot_plan" and c7.get("overall_ok"):
            false_positive = True
        false_negative = (not decision.should_escalate) and would_need

        cases_out.append(
            {
                "case_id": cid,
                "escalated": decision.should_escalate,
                "reason": decision.reason_code,
                "path": path,
                "overall_ok": chosen.get("overall_ok"),
                "safe_outcome": chosen.get("safe_outcome"),
                "unsafe_execution": chosen.get("unsafe_execution"),
                "status": chosen.get("status"),
                "7b_overall_ok": c7.get("overall_ok"),
                "32b_overall_ok": c32.get("overall_ok"),
                "false_positive_escalation": false_positive,
                "false_negative_escalation": false_negative,
                "composite_final": chosen.get("composite_final_result_success"),
                "lookup_final": (
                    bool(chosen.get("overall_ok"))
                    if "lookup_join" in cid
                    else None
                ),
                "three_file_final": chosen.get("three_file_final_result_success"),
                "dirty_final": (
                    bool(chosen.get("overall_ok"))
                    if "dirty_multifile" in cid
                    else None
                ),
                "rename_recovered": (
                    bool(chosen.get("overall_ok"))
                    if "rename_join" in cid
                    else None
                ),
                "scenario": chosen.get("scenario") or c7.get("scenario"),
            }
        )

    n = max(len(cases_out), 1)

    def rate(pred) -> float:
        return round(100.0 * sum(1 for c in cases_out if pred(c)) / n, 2)

    esc_rate = rate(lambda c: c["escalated"])
    # Latency estimate: all cases pay 7B path; escalated also pay 32B planner mean
    # Use Phase 27 means as planner-call proxies (not full suite wall).
    est_latency = LAT_7B + (esc_rate / 100.0) * LAT_32B
    # Rough wall: 7B per-case wall + escalation fraction of 32B per-case wall
    est_wall = WALL_7B_PER_CASE * 19 + (esc_rate / 100.0) * WALL_32B_PER_CASE * 19

    return {
        "run": run_i,
        "n": len(cases_out),
        "metrics": {
            "overall_ok_rate": rate(lambda c: c["overall_ok"]),
            "safe_outcome_rate": rate(lambda c: c["safe_outcome"]),
            "unsafe_execution_rate": rate(lambda c: c["unsafe_execution"]),
            "escalation_rate": esc_rate,
            "strong_planner_invocation_rate": esc_rate,
            "escalation_success_rate": rate(lambda c: c["escalated"] and c["overall_ok"]),
            "false_positive_escalation_rate": rate(lambda c: c["false_positive_escalation"]),
            "false_negative_escalation_rate": rate(lambda c: c["false_negative_escalation"]),
            "composite_final_result_success_rate": _scenario_rate(
                cases_out, "composite_key_join", "composite_final"
            ),
            "lookup_final_result_success_rate": _scenario_rate(
                cases_out, "lookup", "lookup_final", case_id_substr="lookup_join"
            ),
            "three_file_final_result_success_rate": _scenario_rate(
                cases_out, "three_file", "three_file_final", case_id_substr="three_file"
            ),
            "dirty_final_result_success_rate": _scenario_rate(
                cases_out, "dirty", "dirty_final", case_id_substr="dirty_multifile"
            ),
            "estimated_planner_latency_mean_s": round(est_latency, 2),
            "estimated_suite_wall_s": round(est_wall, 1),
        },
        "cases": cases_out,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    strategy = PlannerModelStrategy(
        fast_model="qwen2.5:7b",
        strong_model="qwen3:32b",
        enable_escalation=True,
        strong_max_retries=2,
    )
    runs = [simulate_run(i, strategy) for i in (1, 2, 3)]

    def mean_key(k: str) -> float:
        return round(sum(r["metrics"][k] for r in runs) / len(runs), 2)

    keys = list(runs[0]["metrics"].keys())
    means = {k: mean_key(k) for k in keys}

    comparison = {
        "phase": 28,
        "kind": "offline_frozen_simulation",
        "latency_priors_s": {"7b_planner_mean": LAT_7B, "32b_planner_mean": LAT_32B},
        "strategies": {
            "A_7b_only": {
                "overall": 73.68,
                "safe": 89.47,
                "unsafe": 0.0,
                "strong_invocation": 0.0,
                "estimated_planner_latency_s": LAT_7B,
                "composite_final": 0.0,
                "lookup_final": 0.0,
                "three_file_final": 0.0,
                "dirty_final": 100.0,
            },
            "B_32b_only": {
                "overall": 100.0,
                "safe": 100.0,
                "unsafe": 0.0,
                "strong_invocation": 100.0,
                "estimated_planner_latency_s": LAT_32B,
                "composite_final": 100.0,
                "lookup_final": 100.0,
                "three_file_final": 100.0,
                "dirty_final": 100.0,
            },
            "C_evidence_escalation": {
                "overall": means["overall_ok_rate"],
                "safe": means["safe_outcome_rate"],
                "unsafe": means["unsafe_execution_rate"],
                "strong_invocation": means["escalation_rate"],
                "estimated_planner_latency_s": means["estimated_planner_latency_mean_s"],
                "composite_final": means["composite_final_result_success_rate"],
                "lookup_final": means["lookup_final_result_success_rate"],
                "three_file_final": means["three_file_final_result_success_rate"],
                "dirty_final": means["dirty_final_result_success_rate"],
                "false_positive_escalation": means["false_positive_escalation_rate"],
                "false_negative_escalation": means["false_negative_escalation_rate"],
            },
        },
        "per_run": runs,
        "means": means,
        "recommendation": None,
    }
    c = comparison["strategies"]["C_evidence_escalation"]
    # Live warrant: improves overall, unsafe=0, strong invocation << 100, latency << 32b
    live_ok = (
        c["unsafe"] == 0
        and c["overall"] > 73.68
        and c["strong_invocation"] < 50
        and c["estimated_planner_latency_s"] < LAT_32B * 0.6
    )
    comparison["recommendation"] = {
        "run_live_benchmark": live_ok,
        "rationale": (
            "Escalation recovers exhausted validation failures (lookup/rename) with low "
            "32B rate; silent wrong-success residuals (composite/three_file) remain "
            "false-negatives by design (no golden in production)."
            if live_ok
            else "Simulation did not clear live gate."
        ),
    }

    PHASE28.mkdir(parents=True, exist_ok=True)
    out = PHASE28 / "offline_strategy_simulation.json"
    if args.write or True:
        out.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(comparison["strategies"], ensure_ascii=False, indent=2))
    print("recommendation", comparison["recommendation"])
    print("wrote", out)


if __name__ == "__main__":
    main()
