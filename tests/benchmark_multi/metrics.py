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

    def _true_unsafe_block(c: dict[str, Any]) -> bool:
        return c.get("scenario") in {"many_to_many", "unrelated", "ambiguous_key"} and bool(
            (c.get("levels") or {}).get("L3_plan_safety", {}).get("blocked_unsafe")
        )

    def _vfp(c: dict[str, Any]) -> bool:
        # Expected-success cases incorrectly blocked by plan validator
        if c.get("scenario") in {"many_to_many", "unrelated", "ambiguous_key", "incompatible_union"}:
            return False
        if "success" not in _expected_statuses(c):
            return False
        if c.get("status") == "success":
            return False
        return bool((c.get("levels") or {}).get("L3_plan_safety", {}).get("blocked_unsafe"))

    def _alias_failure(c: dict[str, Any]) -> bool:
        l4 = (c.get("levels") or {}).get("L4_execution") or {}
        if l4.get("missing_columns"):
            return True
        if l4.get("reason") == "result_columns_missing":
            return True
        return "alias_contract_error" in (c.get("failure_categories") or [])

    def _composite_failure(c: dict[str, Any]) -> bool:
        return c.get("scenario") == "composite_key_join" and not c.get("overall_ok")

    def _intermediate_schema_failure(c: dict[str, Any]) -> bool:
        cats = c.get("failure_categories") or []
        if "intermediate_schema_error" in cats:
            return True
        codes = []
        for e in c.get("retry_log") or []:
            codes.extend(e.get("failure_codes") or [])
        return bool(
            set(codes)
            & {
                "nonexistent_column",
                "missing_column",
                "select_columns_mismatch",
                "join_columns_metadata_mismatch",
            }
        )

    validator_fp = rate(_vfp)
    true_unsafe_block = rate(_true_unsafe_block)
    alias_fail = rate(_alias_failure)
    composite_fail = rate(_composite_failure)
    intermediate_fail = rate(_intermediate_schema_failure)
    # rate among three_file cases only
    three_n = sum(1 for c in case_results if c.get("scenario") == "three_file_chain") or 1
    three_file_success = round(
        100.0
        * sum(
            1
            for c in case_results
            if c.get("scenario") == "three_file_chain" and c.get("overall_ok")
        )
        / three_n,
        2,
    )
    join_agg_n = sum(1 for c in case_results if c.get("scenario") == "join_aggregate") or 1
    join_aggregate_contract_success = round(
        100.0
        * sum(
            1
            for c in case_results
            if c.get("scenario") == "join_aggregate" and c.get("overall_ok")
        )
        / join_agg_n,
        2,
    )
    composite_n = sum(1 for c in case_results if c.get("scenario") == "composite_key_join") or 1
    composite_key_success = round(
        100.0
        * sum(
            1
            for c in case_results
            if c.get("scenario") == "composite_key_join" and c.get("overall_ok")
        )
        / composite_n,
        2,
    )
    correct_op_wrong = rate(lambda c: c.get("correct_operation_wrong_result"))
    safe_but_incorrect = rate(lambda c: c.get("safe_but_incorrect"))
    cannot_plan_contract_fail = rate(
        lambda c: "cannot_plan" in _expected_statuses(c) and c.get("status") == "failed"
    )

    # Phase 23 semantic metrics (auxiliary — do not redefine legacy rates)
    success_cases = [c for c in case_results if c.get("status") == "success"]
    n_succ = len(success_cases) or 1

    def rate_succ(pred) -> float:
        return round(100.0 * sum(1 for c in success_cases if pred(c)) / n_succ, 2)

    semantic_result_accuracy = rate_succ(lambda c: c.get("semantic_equivalent"))
    representation_only = rate(lambda c: c.get("representation_only_mismatch"))
    true_wrong = rate(lambda c: c.get("correct_op_semantic_wrong_result"))
    grain_mismatch_rate = rate(lambda c: c.get("correct_op_grain_mismatch"))
    structural_mismatch_rate = rate(lambda c: c.get("correct_op_structural_mismatch"))
    alias_only = rate(lambda c: c.get("alias_only_mismatch"))
    semantic_eq = rate(lambda c: c.get("semantic_equivalent"))
    safe_but_sem_wrong = rate(lambda c: c.get("safe_but_semantically_wrong"))

    def _rate_among(pred_sc, pred_ok) -> float:
        subset = [c for c in case_results if pred_sc(c)]
        n = len(subset) or 1
        return round(100.0 * sum(1 for c in subset if pred_ok(c)) / n, 2)

    composite_key_sel = _rate_among(
        lambda c: c.get("scenario") == "composite_key_join",
        lambda c: c.get("composite_key_selection_success") is True,
    )
    composite_final = _rate_among(
        lambda c: c.get("scenario") == "composite_key_join",
        lambda c: c.get("composite_final_result_success") is True,
    )
    three_join = _rate_among(
        lambda c: c.get("scenario") == "three_file_chain",
        lambda c: c.get("three_file_join_chain_success") is True,
    )
    three_final = _rate_among(
        lambda c: c.get("scenario") == "three_file_chain",
        lambda c: c.get("three_file_final_result_success") is True,
    )
    lookup_final = _rate_among(
        lambda c: c.get("scenario") == "lookup_join",
        lambda c: bool(c.get("overall_ok")),
    )
    dirty_final = _rate_among(
        lambda c: c.get("scenario") == "dirty_multifile",
        lambda c: bool(c.get("overall_ok")),
    )
    composite_join = _rate_among(
        lambda c: c.get("scenario") == "composite_key_join",
        lambda c: c.get("status") == "success"
        and "join" in (c.get("selected_operations") or []),
    )
    unnecessary_agg = rate(
        lambda c: c.get("correct_op_grain_mismatch")
        or "correct_op_grain_mismatch" in (c.get("failure_categories") or [])
    )
    required_field_loss = rate(
        lambda c: c.get("correct_op_structural_mismatch")
        or "correct_op_structural_mismatch" in (c.get("failure_categories") or [])
    )
    safe_but_final_contract = rate(
        lambda c: bool(c.get("safe_outcome"))
        and bool(c.get("safe_but_semantically_wrong"))
        and not bool(c.get("correct_op_semantic_wrong_result"))
    )
    safe_but_true_value = rate(lambda c: c.get("correct_op_semantic_wrong_result"))

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
            "true_unsafe_block_rate": true_unsafe_block,
            "alias_failure_rate": alias_fail,
            "composite_failure_rate": composite_fail,
            "alias_contract_failure_rate": alias_fail,
            "intermediate_schema_failure_rate": intermediate_fail,
            "composite_key_success_rate": composite_key_success,
            "three_file_success_rate": three_file_success,
            "join_aggregate_contract_success_rate": join_aggregate_contract_success,
            "correct_operation_wrong_result_rate": correct_op_wrong,
            "safe_but_incorrect_rate": safe_but_incorrect,
            "cannot_plan_contract_failure_rate": cannot_plan_contract_fail,
            "semantic_result_accuracy": semantic_result_accuracy,
            "representation_only_mismatch_rate": representation_only,
            "true_wrong_result_rate": true_wrong,
            "grain_mismatch_rate": grain_mismatch_rate,
            "structural_result_mismatch_rate": structural_mismatch_rate,
            "alias_only_mismatch_rate": alias_only,
            "semantic_equivalent_rate": semantic_eq,
            "safe_but_semantically_wrong_rate": safe_but_sem_wrong,
            "composite_key_selection_success_rate": composite_key_sel,
            "composite_final_result_success_rate": composite_final,
            "three_file_join_chain_success_rate": three_join,
            "three_file_final_result_success_rate": three_final,
            "lookup_final_result_success_rate": lookup_final,
            "dirty_final_result_success_rate": dirty_final,
            "composite_join_success_rate": composite_join,
            "unnecessary_aggregate_failure_rate": unnecessary_agg,
            "required_field_loss_rate": required_field_loss,
            "safe_but_final_contract_mismatch_rate": safe_but_final_contract,
            "safe_but_true_semantic_value_error_rate": safe_but_true_value,
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
