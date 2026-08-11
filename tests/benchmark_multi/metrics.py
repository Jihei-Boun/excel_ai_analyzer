"""Aggregate multi-file benchmark metrics."""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def summarize_results(
    case_results: list[dict[str, Any]],
    *,
    mode: str,
    model: str | None = None,
) -> dict[str, Any]:
    n = len(case_results) or 1

    def rate(pred) -> float:
        return round(100.0 * sum(1 for c in case_results if pred(c)) / n, 2)

    pipeline_success = rate(lambda c: c.get("status") == "success")
    cannot_plan = rate(lambda c: c.get("status") == "cannot_plan")
    failed = rate(lambda c: c.get("status") == "failed")
    safe_outcome = rate(lambda c: c.get("safe_outcome"))
    unsafe_execution = rate(lambda c: c.get("unsafe_execution"))
    overall_ok = rate(lambda c: c.get("overall_ok"))

    cp_cases = [c for c in case_results if "cannot_plan" in _expected_statuses(c)]
    # From evaluation flags:
    correct_cp = rate(lambda c: c.get("correct_cannot_plan"))
    unnecessary_cp = rate(lambda c: c.get("unnecessary_cannot_plan"))

    first_plan = rate(
        lambda c: (c.get("levels") or {}).get("L6_recovery", {}).get("first_plan_success")
    )
    retry_used = rate(
        lambda c: ((c.get("levels") or {}).get("L6_recovery", {}).get("retry_count") or 0) > 0
    )
    retry_success = rate(
        lambda c: c.get("overall_ok")
        and ((c.get("levels") or {}).get("L6_recovery", {}).get("retry_count") or 0) > 0
    )
    exhausted = rate(lambda c: c.get("status") == "failed")
    duplicate = rate(
        lambda c: ((c.get("levels") or {}).get("L6_recovery", {}).get("duplicate_plan_count") or 0) > 0
        or ((c.get("levels") or {}).get("L6_recovery", {}).get("repeated_plan"))
    )

    plan_val_fail = rate(
        lambda c: ((c.get("levels") or {}).get("L6_recovery", {}).get("plan_validation_failure_count") or 0)
        > 0
    )
    exec_fail = rate(
        lambda c: ((c.get("levels") or {}).get("L6_recovery", {}).get("execution_failure_count") or 0) > 0
    )
    result_val_fail = rate(
        lambda c: (
            (c.get("levels") or {}).get("L6_recovery", {}).get("result_validation_failure_count") or 0
        )
        > 0
    )

    # planner quality
    pq_keys = [
        "wrong_operation",
        "wrong_join_key",
        "wrong_join_how",
        "wrong_join_direction",
        "missing_operation",
        "wrong_composition",
    ]
    planner_quality = {
        k: rate(lambda c, key=k: (c.get("planner_quality") or {}).get(key)) for k in pq_keys
    }

    # join safety
    blocked = rate(
        lambda c: (c.get("levels") or {}).get("L3_plan_safety", {}).get("blocked_unsafe")
    )
    many_block = rate(
        lambda c: c.get("scenario") == "many_to_many"
        and (c.get("levels") or {}).get("L3_plan_safety", {}).get("blocked_unsafe")
    )
    # Validator false positive: expected-success cases blocked by plan validator
    # without unsafe intent (proxy — success expected but blocked and not unsafe scenario)
    def _vfp(c: dict[str, Any]) -> bool:
        if c.get("scenario") in {"many_to_many", "unrelated", "ambiguous_key"}:
            return False
        if "success" not in _expected_statuses(c):
            return False
        if c.get("status") == "success":
            return False
        return bool((c.get("levels") or {}).get("L3_plan_safety", {}).get("blocked_unsafe"))

    validator_fp = rate(_vfp)

    # failure taxonomy
    cat_counts: dict[str, int] = {}
    for c in case_results:
        for cat in c.get("failure_categories") or []:
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

    # scenario breakdown
    scenarios: dict[str, dict[str, Any]] = {}
    for c in case_results:
        sc = str(c.get("scenario") or "generic")
        bucket = scenarios.setdefault(sc, {"n": 0, "ok": 0, "safe": 0, "unsafe": 0})
        bucket["n"] += 1
        bucket["ok"] += int(bool(c.get("overall_ok")))
        bucket["safe"] += int(bool(c.get("safe_outcome")))
        bucket["unsafe"] += int(bool(c.get("unsafe_execution")))
    for sc, b in scenarios.items():
        nn = max(b["n"], 1)
        b["overall_ok_rate"] = round(100.0 * b["ok"] / nn, 2)
        b["safe_outcome_rate"] = round(100.0 * b["safe"] / nn, 2)
        b["unsafe_execution_rate"] = round(100.0 * b["unsafe"] / nn, 2)

    domains: dict[str, dict[str, Any]] = {}
    for c in case_results:
        d = str(c.get("domain") or "generic")
        bucket = domains.setdefault(d, {"n": 0, "ok": 0, "safe": 0})
        bucket["n"] += 1
        bucket["ok"] += int(bool(c.get("overall_ok")))
        bucket["safe"] += int(bool(c.get("safe_outcome")))
    for d, b in domains.items():
        nn = max(b["n"], 1)
        b["overall_ok_rate"] = round(100.0 * b["ok"] / nn, 2)
        b["safe_outcome_rate"] = round(100.0 * b["safe"] / nn, 2)

    return {
        "mode": mode,
        "model": model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(case_results),
        "overall": {
            "overall_ok_rate": overall_ok,
            "pipeline_success_rate": pipeline_success,
            "cannot_plan_rate": cannot_plan,
            "failed_rate": failed,
            "safe_outcome_rate": safe_outcome,
            "unsafe_execution_rate": unsafe_execution,
            "correct_cannot_plan_rate": correct_cp,
            "unnecessary_cannot_plan_rate": unnecessary_cp,
            "first_plan_success_rate": first_plan,
            "retry_rate": retry_used,
            "retry_success_rate": retry_success,
            "retry_exhausted_rate": exhausted,
            "duplicate_plan_rate": duplicate,
            "plan_validation_failure_rate": plan_val_fail,
            "execution_failure_rate": exec_fail,
            "result_validation_failure_rate": result_val_fail,
            "unsafe_join_block_rate": blocked,
            "many_to_many_block_rate": many_block,
            "validator_false_positive_rate": validator_fp,
        },
        "planner_quality": planner_quality,
        "failure_categories": cat_counts,
        "scenarios": scenarios,
        "domains": domains,
        "cases": case_results,
        "meta": {"cp_case_hint_count": len(cp_cases)},
    }


def _expected_statuses(case_result: dict[str, Any]) -> list[str]:
    # not stored; infer from flags
    if case_result.get("correct_cannot_plan") or case_result.get("status") == "cannot_plan":
        return ["cannot_plan"]
    return ["success"]


def save_summary(summary: dict[str, Any], results_dir: Path) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    mode = summary.get("model") or summary.get("mode") or "run"
    path = results_dir / f"{ts}_{mode}.json"
    # strip heavy frames if any
    slim = json.loads(json.dumps(summary, ensure_ascii=False, default=str))
    path.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = results_dir / "latest.json"
    latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def summarize_multi_run(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    keys = [
        "pipeline_success_rate",
        "safe_outcome_rate",
        "unsafe_execution_rate",
        "first_plan_success_rate",
        "retry_success_rate",
        "cannot_plan_rate",
        "overall_ok_rate",
    ]
    pq_keys = ["wrong_join_key", "wrong_operation", "wrong_composition"]

    def series(path_keys: list[str]) -> list[float]:
        vals = []
        for s in summaries:
            cur: Any = s.get("overall") or {}
            for k in path_keys:
                cur = (cur or {}).get(k) if isinstance(cur, dict) else None
            if cur is not None:
                vals.append(float(cur))
        return vals

    out: dict[str, Any] = {"runs": len(summaries), "metrics": {}}
    for k in keys:
        vals = series([k])
        out["metrics"][k] = _mmms(vals)
    pq: dict[str, Any] = {}
    for k in pq_keys:
        vals = []
        for s in summaries:
            vals.append(float(((s.get("planner_quality") or {}).get(k) or 0.0)))
        pq[k] = _mmms(vals)
    out["planner_quality"] = pq
    return out


def _mmms(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "std": 0.0}
    return {
        "mean": round(statistics.mean(vals), 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "std": round(statistics.pstdev(vals), 2) if len(vals) > 1 else 0.0,
    }
