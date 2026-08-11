"""Multi-file benchmark evaluation (Level 1–6). No production side effects."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from tests.benchmark_multi.schema import MultiBenchmarkCase


def evaluate_case(
    case: MultiBenchmarkCase,
    *,
    pipeline: Any,
    understanding: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return structured evaluation for one case."""
    levels: dict[str, Any] = {
        "L1_understanding": {},
        "L2_plan": {},
        "L3_plan_safety": {},
        "L4_execution": {},
        "L5_result_validation": {},
        "L6_recovery": {},
    }
    categories: list[str] = []
    planner_quality: dict[str, bool] = {}

    status = str(getattr(pipeline, "status", None) or "failed")
    plan = getattr(pipeline, "plan", None)
    plan_dict = plan.to_dict() if plan is not None and hasattr(plan, "to_dict") else None
    plan_val = getattr(pipeline, "plan_validation", None)
    execution = getattr(pipeline, "execution", None)
    result_val = getattr(pipeline, "result_validation", None)
    meta = dict(getattr(pipeline, "metadata", None) or {})
    retry_log = list(getattr(pipeline, "retry_log", None) or [])
    final_df = getattr(pipeline, "final_output", None)

    # ---- status / safety ----
    expected_statuses = list(case.expected.pipeline_status)
    status_ok = status in expected_statuses
    levels["pipeline_status_ok"] = status_ok
    levels["pipeline_status"] = status

    unsafe = _is_unsafe_execution(case, status=status, plan_dict=plan_dict, execution=execution)
    safety_ok = (not unsafe) if case.expected.safety_outcome == "safe" else unsafe
    # For expected safe: must not be unsafe AND status must be in allowed (or blocked safely)
    if case.expected.safety_outcome == "safe":
        safety_ok = (not unsafe) and (
            status_ok
            or (
                case.expected.allow_plan_validation_block
                and status in {"failed", "cannot_plan"}
                and _plan_validation_blocked(plan_val, retry_log)
            )
        )
    levels["safe_outcome"] = bool(safety_ok)
    levels["unsafe_execution"] = bool(unsafe)

    # cannot_plan correctness
    cp_expected = "cannot_plan" in expected_statuses
    cp_actual = status == "cannot_plan"
    levels["correct_cannot_plan"] = bool(cp_expected and cp_actual)
    levels["unnecessary_cannot_plan"] = bool(
        (not cp_expected) and cp_actual and "success" in expected_statuses
    )

    # ---- L1 relationship ----
    levels["L1_understanding"] = _eval_relationship(case, understanding)

    # ---- L2 plan ----
    levels["L2_plan"] = _eval_plan(case, plan_dict, planner_quality)

    # ---- L3 plan safety ----
    levels["L3_plan_safety"] = _eval_plan_safety(case, plan_val, retry_log, plan_dict)

    # ---- L4 execution / golden ----
    levels["L4_execution"] = _eval_execution(case, status, execution, final_df)

    # ---- L5 result validation ----
    levels["L5_result_validation"] = _eval_result_validation(result_val, retry_log)

    # ---- L6 recovery ----
    levels["L6_recovery"] = _eval_recovery(meta, retry_log)

    # failure categories
    categories.extend(_classify(case, status, levels, plan_dict, planner_quality, unsafe))

    overall_ok = bool(status_ok and safety_ok and levels["L4_execution"].get("ok", True))
    # For cannot_plan expected cases, L4 may be N/A
    if cp_expected and cp_actual and safety_ok:
        overall_ok = True
    if case.expected.allow_plan_validation_block and levels["L3_plan_safety"].get("blocked_unsafe"):
        if not unsafe:
            overall_ok = status_ok or status in {"failed", "cannot_plan"}

    return {
        "case_id": case.id,
        "scenario": case.scenario,
        "domain": case.domain,
        "status": status,
        "status_ok": status_ok,
        "overall_ok": overall_ok,
        "safe_outcome": safety_ok,
        "unsafe_execution": unsafe,
        "correct_cannot_plan": levels["correct_cannot_plan"],
        "unnecessary_cannot_plan": levels["unnecessary_cannot_plan"],
        "levels": levels,
        "planner_quality": planner_quality,
        "failure_categories": categories,
        "selected_operations": _ops(plan_dict),
        "retry_log": retry_log,
        "metadata": {
            "attempt_count": meta.get("attempt_count"),
            "retry_count": meta.get("retry_count"),
            "duplicate_plan_count": meta.get("duplicate_plan_count"),
            "final_shape": meta.get("final_shape"),
        },
    }


def _ops(plan_dict: dict[str, Any] | None) -> list[str]:
    if not plan_dict:
        return []
    return [str(s.get("op")) for s in (plan_dict.get("steps") or []) if isinstance(s, dict)]


def _plan_validation_blocked(plan_val: Any, retry_log: list[dict[str, Any]]) -> bool:
    if plan_val is not None and hasattr(plan_val, "valid") and not plan_val.valid:
        return True
    return any(e.get("failure_stage") == "integration_plan_validation" for e in retry_log)


def _is_unsafe_execution(
    case: MultiBenchmarkCase,
    *,
    status: str,
    plan_dict: dict[str, Any] | None,
    execution: Any,
) -> bool:
    """Unsafe = delivered a successful integration that should not have been accepted."""
    if status != "success":
        return False
    ops = _ops(plan_dict)
    for fo in case.expected.forbidden_operations:
        if fo in ops:
            return True
    # Expected cannot_plan / failed but succeeded
    if "success" not in case.expected.pipeline_status:
        return True
    # many-to-many scenario with join success
    if case.scenario == "many_to_many" and "join" in ops:
        return True
    if case.scenario == "unrelated" and ("join" in ops or "union_rows" in ops):
        return True
    if case.scenario == "ambiguous_key" and "join" in ops:
        return True
    # Extreme actual amplification still marked success
    if execution is not None:
        for sr in getattr(execution, "step_results", None) or []:
            amp = (getattr(sr, "metadata", None) or {}).get("actual_amplification_ratio")
            if amp is not None and float(amp) >= 10.0:
                return True
    return False


def _eval_relationship(case: MultiBenchmarkCase, understanding: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"checked": False, "ok": True}
    if not case.expected.relationship_allowed and not case.expected.relationship_forbidden:
        return out
    if not understanding:
        out["ok"] = False
        out["reason"] = "missing_understanding"
        return out
    out["checked"] = True
    labels = [
        str(r.get("relationship"))
        for r in (understanding.get("relationships") or [])
        if isinstance(r, dict)
    ]
    out["observed"] = labels
    if case.expected.relationship_allowed:
        out["ok"] = any(lbl in case.expected.relationship_allowed for lbl in labels) if labels else False
    if case.expected.relationship_forbidden:
        if any(lbl in case.expected.relationship_forbidden for lbl in labels):
            out["ok"] = False
            out["forbidden_hit"] = True
    return out


def _eval_plan(
    case: MultiBenchmarkCase,
    plan_dict: dict[str, Any] | None,
    planner_quality: dict[str, bool],
) -> dict[str, Any]:
    out: dict[str, Any] = {"checked": bool(plan_dict), "ok": True}
    if not plan_dict:
        if "cannot_plan" in case.expected.pipeline_status:
            return out
        out["ok"] = False
        return out
    ops = _ops(plan_dict)
    out["operations"] = ops
    missing = [op for op in case.expected.required_operations if op not in ops]
    forbidden_hit = [op for op in case.expected.forbidden_operations if op in ops]
    out["missing_operations"] = missing
    out["forbidden_hit"] = forbidden_hit
    if missing:
        out["ok"] = False
        planner_quality["missing_operation"] = True
    if forbidden_hit:
        out["ok"] = False
        planner_quality["wrong_operation"] = True

    # join key checks only when expected keys provided (unambiguous cases)
    if case.expected.join_left_keys or case.expected.join_right_keys:
        join_steps = [
            s for s in (plan_dict.get("steps") or []) if isinstance(s, dict) and s.get("op") == "join"
        ]
        if not join_steps:
            if "join" in case.expected.required_operations:
                planner_quality["missing_operation"] = True
                out["ok"] = False
        else:
            params = join_steps[0].get("params") or {}
            lk = [str(x) for x in (params.get("left_keys") or [])]
            rk = [str(x) for x in (params.get("right_keys") or [])]
            if case.expected.join_left_keys and lk != case.expected.join_left_keys:
                planner_quality["wrong_join_key"] = True
                out["ok"] = False
            if case.expected.join_right_keys and rk != case.expected.join_right_keys:
                planner_quality["wrong_join_key"] = True
                out["ok"] = False
            if case.expected.join_how:
                how = str(params.get("how") or "")
                if how not in case.expected.join_how:
                    planner_quality["wrong_join_how"] = True
                    out["ok"] = False
            # direction: inputs[0] should be first required file if provided
            if case.expected.required_input_files and len(case.expected.required_input_files) >= 2:
                inputs = [str(x) for x in (join_steps[0].get("inputs") or [])]
                # source ids without .xlsx
                expected_left = case.expected.required_input_files[0].replace(".xlsx", "")
                if inputs and inputs[0] != expected_left and inputs[0] not in case.files[0]:
                    # soft: only flag if clearly reversed vs files order expectation
                    if len(inputs) == 2 and inputs[0].endswith(
                        case.expected.required_input_files[1].replace(".xlsx", "")
                    ):
                        planner_quality["wrong_join_direction"] = True

    # composition: required ops subsequence
    if len(case.expected.required_operations) >= 2:
        it = iter(ops)
        if not all(op in it for op in case.expected.required_operations):
            # allow extras between; check order
            idx = 0
            ok_order = True
            for need in case.expected.required_operations:
                try:
                    idx = ops.index(need, idx) + 1
                except ValueError:
                    ok_order = False
                    break
            if not ok_order:
                planner_quality["wrong_composition"] = True
                out["ok"] = False
    return out


def _eval_plan_safety(
    case: MultiBenchmarkCase,
    plan_val: Any,
    retry_log: list[dict[str, Any]],
    plan_dict: dict[str, Any] | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"checked": True, "ok": True, "blocked_unsafe": False}
    codes: list[str] = []
    if plan_val is not None and hasattr(plan_val, "errors"):
        codes.extend([e.code for e in plan_val.errors])
    for e in retry_log:
        if e.get("failure_stage") == "integration_plan_validation":
            codes.extend(list(e.get("failure_codes") or []))
    out["error_codes"] = codes
    block_codes = {
        "many_to_many_join_risk",
        "extreme_row_amplification",
        "join_against_unrelated",
        "ambiguous_key_selection",
        "insufficient_evidence_forced_join",
        "union_incompatible_schema",
    }
    if any(c in block_codes for c in codes):
        out["blocked_unsafe"] = True
    if case.scenario in {"many_to_many", "unrelated", "ambiguous_key"} and case.expected.allow_plan_validation_block:
        # safety success if blocked or cannot_plan
        out["ok"] = out["blocked_unsafe"] or True
    return out


def _eval_execution(
    case: MultiBenchmarkCase,
    status: str,
    execution: Any,
    final_df: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {"checked": status == "success", "ok": True}
    if status != "success":
        out["ok"] = True  # N/A
        out["skipped"] = True
        return out
    if execution is not None and hasattr(execution, "success") and not execution.success:
        out["ok"] = False
        out["reason"] = "execution_failed"
        return out
    if not isinstance(final_df, pd.DataFrame):
        out["ok"] = False
        out["reason"] = "missing_final_df"
        return out

    spec = case.expected.result
    df = final_df
    if spec.sort_by:
        cols = [c for c in spec.sort_by if c in df.columns]
        if cols:
            df = df.sort_values(cols).reset_index(drop=True)

    if spec.expected_row_count is not None and int(len(df)) != int(spec.expected_row_count):
        out["ok"] = False
        out["row_count"] = int(len(df))
        out["expected_row_count"] = spec.expected_row_count

    missing_cols = [c for c in spec.required_columns if c not in df.columns]
    if missing_cols:
        out["ok"] = False
        out["missing_columns"] = missing_cols

    if spec.expected_result and spec.key_column and spec.value_column:
        if spec.key_column not in df.columns or spec.value_column not in df.columns:
            out["ok"] = False
            out["reason"] = "result_columns_missing"
        else:
            got = {
                str(k): float(v) if isinstance(v, (int, float, np.floating)) else v
                for k, v in zip(df[spec.key_column], df[spec.value_column])
            }
            for k, exp in spec.expected_result.items():
                if str(k) not in got:
                    out["ok"] = False
                    out.setdefault("missing_keys", []).append(str(k))
                    continue
                actual = got[str(k)]
                if isinstance(exp, (int, float)) and isinstance(actual, (int, float, np.floating)):
                    if not np.isclose(float(actual), float(exp), rtol=spec.rtol, atol=spec.atol):
                        out["ok"] = False
                        out.setdefault("value_mismatches", {})[str(k)] = {
                            "expected": exp,
                            "actual": float(actual),
                        }
                elif actual != exp:
                    out["ok"] = False
                    out.setdefault("value_mismatches", {})[str(k)] = {
                        "expected": exp,
                        "actual": actual,
                    }
            out["observed_result"] = got
    return out


def _eval_result_validation(result_val: Any, retry_log: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"checked": result_val is not None, "ok": True}
    if result_val is None:
        # may have failed earlier
        out["ok"] = True
        return out
    out["valid"] = bool(getattr(result_val, "valid", True))
    out["error_codes"] = [e.code for e in getattr(result_val, "errors", []) or []]
    out["warning_codes"] = [w.code for w in getattr(result_val, "warnings", []) or []]
    if any(e.get("failure_stage") == "integration_result_validation" for e in retry_log):
        out["had_result_retry"] = True
    return out


def _eval_recovery(meta: dict[str, Any], retry_log: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "attempt_count": meta.get("attempt_count"),
        "retry_count": meta.get("retry_count") or len(retry_log),
        "first_plan_success": bool(meta.get("first_plan_success")),
        "duplicate_plan_count": meta.get("duplicate_plan_count") or 0,
        "plan_validation_failure_count": meta.get("plan_validation_failure_count") or 0,
        "execution_failure_count": meta.get("execution_failure_count") or 0,
        "result_validation_failure_count": meta.get("result_validation_failure_count") or 0,
        "stages": [e.get("failure_stage") for e in retry_log],
        "repeated_plan": any("repeated_plan" in (e.get("failure_codes") or []) for e in retry_log),
    }


def _classify(
    case: MultiBenchmarkCase,
    status: str,
    levels: dict[str, Any],
    plan_dict: dict[str, Any] | None,
    planner_quality: dict[str, bool],
    unsafe: bool,
) -> list[str]:
    cats: list[str] = []
    if unsafe:
        cats.append("unsafe_join" if "join" in _ops(plan_dict) else "unsafe_execution")
    if levels.get("unnecessary_cannot_plan"):
        cats.append("unnecessary_cannot_plan")
    if not levels.get("status_ok") and status == "failed":
        stages = levels.get("L6_recovery", {}).get("stages") or []
        if any(s == "integration_plan_validation" for s in stages):
            cats.append("plan_validation_error")
        cats.append("retry_exhausted")
    for k, v in planner_quality.items():
        if v:
            cats.append(k)
    if not levels["L1_understanding"].get("ok", True) and levels["L1_understanding"].get("checked"):
        cats.append("relationship_error")
    if not levels["L4_execution"].get("ok", True) and not levels["L4_execution"].get("skipped"):
        cats.append("wrong_result")
    if status == "failed" and not cats:
        cats.append("retry_exhausted")
    return sorted(set(cats))
