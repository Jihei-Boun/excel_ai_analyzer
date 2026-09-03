"""Post-Phase-40 Step 4 — retry mechanics causal study (research only).

Counterfactual planner invocations. Does NOT modify core/, retry policy,
cannot_plan semantics, or escalation routing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_pipeline import (
    DEFAULT_MAX_RETRIES,
    _run_integration_attempt_loop,
)
from core.integrate.integration_plan_types import (
    integration_operation_family_signature,
    integration_plan_from_dict,
)
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.integration_planner import build_integration_plan
from core.integrate.integration_result_validate import validate_integration_result
from core.integrate.integration_validation_types import format_integration_validation_feedback
from core.integrate.planner_model_strategy import (
    EscalationDecision,
    PlannerModelStrategy,
    build_escalation_feedback,
    should_escalate_after_fast_path,
)
from core.integrate.relationship_infer import build_cross_file_understanding
from core.integrate.semantic_escalation import (
    SEMANTIC_VERIFIER_MODEL,
    run_integration_pipeline_semantic_experimental,
)
from core.shadow.config import load_shadow_config
from tests.benchmark_multi.phase40_residual import (
    BASE_URL,
    HEAD_EXPECTED,
    build_fresh_corpus,
    extract_row,
    production_config,
)
from tests.benchmark_multi.phase40_residual_rootcause import _manual_semantic_ok
from tests.benchmark_multi.phase40_residual_rootcause_plans import case_by_id

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results" / "multi" / "phase40_retry_causal"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "phase40_retry_causal"
STEP3_UND = ROOT / "benchmark_results" / "multi" / "phase40_residual_rootcause" / "understanding"
STEP3_CAPTURE = (
    ROOT / "benchmark_results" / "multi" / "phase40_residual_rootcause" / "planner_capture"
)
CAPTURE_DIR = OUT / "planner_capture"

FAMILY_LOCK_NEEDLE = "keep the same integration strategy family"
UNLOCKED_RETRY_LINE = (
    "Repair the invalid plan using any IntegrationPlan operations already "
    "available in the DSL; validation issues must be resolved. "
    "Do not invent keys, columns, or sources. "
    "Do not copy the previous invalid plan unchanged."
)

R1_CASES = (
    "r40-D03",
    "r40-D04",
    "r40-D01",
    "r40-G03",
    "CTRL-D03-SPLIT",
)
R3_CASES = ("r40-B02", "r40-D03", "r40-D04")
GENUINE_CP = ("r40-E01", "r40-E02")
B02_REPEAT_N = 5
R1_N = 3
R3_N_B02 = 5
R3_N_D = 3
G01_REPEAT_N = 3
GENUINE_N = 2

FORBIDDEN_UNLOCK_HINTS = (
    "filter side",
    "use filter",
    "use join",
    "split actual",
    "split forecast",
    "grain=group",
    "grain=entity",
)


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def unlock_family_lock_feedback(feedback: list[str]) -> list[str]:
    """COUNTERFACTUAL: drop only the keep-family repair instruction."""
    out: list[str] = []
    replaced = False
    for line in feedback:
        if FAMILY_LOCK_NEEDLE in line:
            out.append(UNLOCKED_RETRY_LINE)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(UNLOCKED_RETRY_LINE)
    joined = "\n".join(out).lower()
    for hint in FORBIDDEN_UNLOCK_HINTS:
        if hint in joined:
            raise RuntimeError(f"counterfactual leaked hint: {hint}")
    return out


def research_family(plan: dict[str, Any] | None) -> str:
    if not isinstance(plan, dict) or plan.get("status") != "planned":
        if isinstance(plan, dict) and plan.get("status") == "cannot_plan":
            return "CANNOT_PLAN"
        return "OTHER"
    ops = [
        str(s.get("op") or "")
        for s in (plan.get("steps") or [])
        if isinstance(s, dict)
    ]
    n_join = ops.count("join")
    n_union = ops.count("union_rows")
    n_filter = ops.count("filter_rows")
    n_agg = ops.count("aggregate")
    n_rename = ops.count("rename_columns")
    metrics = []
    for s in plan.get("steps") or []:
        if isinstance(s, dict) and s.get("op") == "aggregate":
            metrics.extend((s.get("params") or {}).get("metrics") or [])
    aliases = [m.get("alias") for m in metrics if isinstance(m, dict)]
    cols = [m.get("column") for m in metrics if isinstance(m, dict)]
    if n_agg and len(set(cols)) == 1 and len([a for a in aliases if a]) >= 2:
        return "SAME_METRIC_DUAL_AGGREGATE"
    if n_filter >= 2 and n_join and n_rename and not n_agg:
        return "FILTER_RENAME_JOIN"
    if n_filter >= 2 and n_join and not n_agg:
        return "FILTER_JOIN"
    if n_filter and n_join and n_agg:
        return "FILTER_JOIN_AGGREGATE"
    if n_join and n_agg:
        return "JOIN_THEN_AGGREGATE"
    if n_union and n_agg:
        return "UNION_THEN_AGGREGATE"
    if n_union and not n_agg:
        return "UNION_ONLY"
    if n_join and n_rename and not n_agg:
        return "JOIN_RENAME"
    if n_join and not n_agg and not n_union:
        return "JOIN_ONLY"
    if n_agg and not n_join and not n_union:
        return "DIRECT_AGGREGATE"
    return "OTHER"


REFERENCE_FAMILY = {
    "r40-B02": "DIRECT_AGGREGATE",
    "r40-G01": "FILTER_JOIN_AGGREGATE",
    "r40-D03": "FILTER_RENAME_JOIN",
    "r40-D04": "FILTER_RENAME_JOIN",
    "r40-F01": "FILTER_RENAME_JOIN",
    "r40-F02": "FILTER_RENAME_JOIN",
    "r40-F03": "FILTER_RENAME_JOIN",
    "r40-D01": "JOIN_RENAME",
    "CTRL-D03-SPLIT": "JOIN_RENAME",
    "r40-G03": "JOIN_THEN_AGGREGATE",
}

INCOMPATIBLE_IF_INITIAL = {
    "r40-D03": {"UNION_ONLY", "UNION_THEN_AGGREGATE", "JOIN_THEN_AGGREGATE", "SAME_METRIC_DUAL_AGGREGATE", "JOIN_ONLY"},
    "r40-D04": {"UNION_ONLY", "UNION_THEN_AGGREGATE", "JOIN_THEN_AGGREGATE", "SAME_METRIC_DUAL_AGGREGATE", "JOIN_ONLY"},
    "r40-D01": {"UNION_ONLY", "UNION_THEN_AGGREGATE", "DIRECT_AGGREGATE"},
    "CTRL-D03-SPLIT": {"UNION_ONLY", "UNION_THEN_AGGREGATE"},
    "r40-G03": set(),  # family often already join+agg; keys may be wrong
    "r40-F01": {"JOIN_THEN_AGGREGATE", "SAME_METRIC_DUAL_AGGREGATE"},
}


def family_compatible(case_id: str, family: str) -> bool:
    ref = REFERENCE_FAMILY.get(case_id)
    if family == ref:
        return True
    if case_id in {"r40-D03", "r40-D04", "r40-F01", "r40-F02", "r40-F03"}:
        return family in {"FILTER_RENAME_JOIN", "FILTER_JOIN"}
    if case_id in {"r40-D01", "CTRL-D03-SPLIT"}:
        return family in {"JOIN_RENAME", "JOIN_ONLY"}
    if case_id == "r40-B02":
        return family == "DIRECT_AGGREGATE"
    if case_id == "r40-G01":
        return family in {"FILTER_JOIN_AGGREGATE", "FILTER_JOIN"}
    if case_id == "r40-G03":
        return family == "JOIN_THEN_AGGREGATE"
    return False


def enable_capture(tag: str) -> None:
    os.environ["MULTI_PLANNER_CAPTURE_ENABLED"] = "true"
    os.environ["MULTI_PLANNER_CAPTURE_DIR"] = str(CAPTURE_DIR)
    os.environ["MULTI_PLANNER_CAPTURE_REQUEST_ID"] = tag
    os.environ["MULTI_PLANNER_CAPTURE_CASE_ID"] = tag


def load_case(case_id: str) -> dict[str, Any]:
    if case_id.startswith("CTRL-") or case_id.startswith("r40-"):
        try:
            return case_by_id(case_id)
        except KeyError:
            pass
    for c in build_fresh_corpus():
        if c["case_id"] == case_id:
            return c
    raise KeyError(case_id)


def load_understanding(case: dict[str, Any]) -> dict[str, Any]:
    path = STEP3_UND / f"{case['case_id']}.json"
    if path.is_file():
        blob = json.loads(path.read_text(encoding="utf-8"))
        und = blob.get("understanding")
        if isinstance(und, dict) and und.get("file_profiles"):
            return und
    built = build_cross_file_understanding(
        list(case["files"].items()),
        base_url=BASE_URL,
        model="qwen2.5:7b",
        infer_relationships=True,
    )
    return built.to_dict() if hasattr(built, "to_dict") else dict(built)


def first_captured_a0(case_id: str) -> dict[str, Any] | None:
    if not STEP3_CAPTURE.is_dir():
        return None
    for path in sorted(STEP3_CAPTURE.glob("planner_invocations_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("case_id") != case_id:
                continue
            if rec.get("planner_type") != "FAST_INITIAL":
                continue
            plan = rec.get("parsed_plan")
            if isinstance(plan, dict) and plan.get("status") == "planned":
                return plan
    return None


def evaluate_plan(case: dict[str, Any], plan_dict: dict[str, Any] | None, und: dict[str, Any]) -> dict[str, Any]:
    files = {str(k): v.copy() for k, v in case["files"].items()}
    out: dict[str, Any] = {
        "status": None,
        "family": research_family(plan_dict),
        "prod_family": None,
        "grain": None,
        "ops": [],
        "validator_valid": None,
        "validator_codes": [],
        "executor_success": None,
        "result_valid": None,
        "manual_ok": False,
        "cannot_plan_reason": None,
        "parse_ok": isinstance(plan_dict, dict),
    }
    if not isinstance(plan_dict, dict):
        return out
    out["status"] = plan_dict.get("status")
    out["grain"] = ((plan_dict.get("final_output_requirements") or {}) or {}).get("grain")
    out["ops"] = [s.get("op") for s in (plan_dict.get("steps") or []) if isinstance(s, dict)]
    out["prod_family"] = integration_operation_family_signature(plan_dict)
    if plan_dict.get("status") == "cannot_plan":
        out["cannot_plan_reason"] = plan_dict.get("reason")
        return out
    try:
        plan = integration_plan_from_dict(plan_dict)
    except Exception as exc:  # noqa: BLE001
        out["parse_ok"] = False
        out["cannot_plan_reason"] = str(exc)
        return out
    val = validate_integration_plan(und, plan, user_prompt=case["user_prompt"], frames=files)
    out["validator_valid"] = bool(val.valid)
    out["validator_codes"] = [e.code for e in val.errors]
    if not val.valid:
        return out
    exe = execute_integration_plan(files, plan, val)
    out["executor_success"] = bool(exe.success)
    if not exe.success:
        return out
    res = validate_integration_result(plan, exe, plan_validation=val)
    out["result_valid"] = bool(res.valid)
    out["result_codes"] = [e.code for e in res.errors]
    if res.valid and isinstance(exe.final_output, pd.DataFrame):
        out["manual_ok"] = bool(_manual_semantic_ok(case["case_id"], exe.final_output).get("ok"))
        out["n_rows"] = int(len(exe.final_output))
        out["columns"] = [str(c) for c in exe.final_output.columns]
    return out


def classify_cannot_plan(plan: dict[str, Any] | None, *, answerable: bool) -> str:
    if not isinstance(plan, dict) or plan.get("status") != "cannot_plan":
        return "NA"
    reason = str(plan.get("reason") or "")
    if reason == "planner_parse_failed":
        return "CP3_FORMAT_COLLAPSE"
    if not answerable and reason not in {"planner_parse_failed"}:
        return "CP1_SEMANTIC_UNANSWERABLE"
    if reason and reason != "planner_parse_failed":
        return "CP2_EXPLICIT_MODEL_UNANSWERABLE"
    return "CP5_UNKNOWN"


def classify_parse_failure(cap: dict[str, Any]) -> str | None:
    if cap.get("parse_ok") and cap.get("raw_outcome") not in {"FORMAT_FAILURE", "BACKEND_FAILURE"}:
        plan = cap.get("parsed_plan") or {}
        if isinstance(plan, dict) and plan.get("reason") == "planner_parse_failed":
            return "PF2_VALID_JSON_INVALID_PLAN_STRUCTURE"
        return None
    err = str(cap.get("parse_error") or "")
    backend = str(cap.get("backend_error") or "")
    raw = cap.get("raw_model_response_text") or cap.get("raw_model_response")
    if backend and (raw in {None, "", "null"}):
        return "PF5_EMPTY_OR_BACKEND_OUTPUT"
    if "Timeout" in backend or "timeout" in backend.lower():
        return "PF5_EMPTY_OR_BACKEND_OUTPUT"
    if isinstance(raw, dict) or (isinstance(raw, str) and raw.strip().startswith("{")):
        if err:
            return "PF2_VALID_JSON_INVALID_PLAN_STRUCTURE"
        plan = cap.get("parsed_plan") or {}
        if isinstance(plan, dict) and plan.get("status") == "cannot_plan":
            return "PF3_VALID_CANNOT_PLAN_OBJECT"
        return "PF2_VALID_JSON_INVALID_PLAN_STRUCTURE"
    if not raw:
        return "PF5_EMPTY_OR_BACKEND_OUTPUT"
    txt = raw if isinstance(raw, str) else json.dumps(raw, default=str)
    if len(txt) > 20 and not txt.strip().endswith("}"):
        return "PF4_TRUNCATED_OUTPUT"
    return "PF1_INVALID_JSON"


def state_machine_spec() -> dict[str, Any]:
    """Code-derived frozen retry/escalation diagram (research documentation)."""
    return {
        "head": HEAD_EXPECTED,
        "defaults": {
            "pipeline_max_retries": DEFAULT_MAX_RETRIES,
            "pipeline_rounds": DEFAULT_MAX_RETRIES + 1,
            "planner_max_parse_retries": 1,
            "planner_total_parse_attempts": 3,
            "strong_max_retries": 2,
        },
        "transitions": [
            {
                "from": "pipeline_attempt",
                "fn": "core.integrate.integration_pipeline._run_integration_attempt_loop",
                "call": "build_integration_plan(..., retry_feedback=attempt_feedback)",
            },
            {
                "from": "planner_llm",
                "fn": "core.integrate.integration_planner.build_integration_plan",
                "condition": "IntegrationPlanParseError",
                "to": "format_retry",
                "note": "attempt>0 appends Fix JSON shape/types; if still unsure return cannot_plan",
            },
            {
                "from": "format_retry_exhausted",
                "fn": "build_integration_plan",
                "output_status": "cannot_plan",
                "reason": "planner_parse_failed",
            },
            {
                "from": "plan.status==cannot_plan",
                "fn": "_run_integration_attempt_loop",
                "output_status": "cannot_plan",
                "note": "returns immediately; does not set metadata.exhausted",
            },
            {
                "from": "plan.status==planned AND invalid",
                "fn": "format_integration_validation_feedback",
                "retry_mode": "repair for STRUCTURAL/ALIAS (keep family sentence)",
                "output_status": "continue loop",
            },
            {
                "from": "loop ends without success/cannot_plan",
                "fn": "_run_integration_attempt_loop",
                "output_status": "failed",
                "metadata_exhausted": True,
            },
            {
                "from": "fast.status==cannot_plan",
                "fn": "should_escalate_after_fast_path",
                "output": "should_escalate=False",
                "reason_code": "skip_cannot_plan",
            },
            {
                "from": "fast.status==failed AND trigger codes",
                "fn": "should_escalate_after_fast_path",
                "output": "should_escalate=True",
                "call": "_run_integration_attempt_loop(model=qwen3:32b, initial_feedback=build_escalation_feedback)",
            },
        ],
        "keep_family_sentence_file": "core/integrate/integration_validation_types.py",
        "keep_family_function": "format_integration_validation_feedback",
    }


def _a0_and_feedback(case: dict[str, Any], und: dict[str, Any]) -> dict[str, Any]:
    files = {str(k): v.copy() for k, v in case["files"].items()}
    captured = first_captured_a0(case["case_id"])
    if captured is not None:
        plan_d = captured
        source = "step3_capture"
    else:
        enable_capture(f"CF-A0-{case['case_id']}")
        plan = build_integration_plan(
            case["user_prompt"],
            und,
            base_url=BASE_URL,
            model="qwen2.5:7b",
            retry_feedback=None,
        )
        plan_d = plan.to_dict()
        source = "live_a0"
    frozen_fb: list[str] = []
    val_codes: list[str] = []
    valid = None
    if plan_d.get("status") == "planned":
        plan_obj = integration_plan_from_dict(plan_d)
        val = validate_integration_plan(
            und, plan_obj, user_prompt=case["user_prompt"], frames=files
        )
        valid = bool(val.valid)
        val_codes = [e.code for e in val.errors]
        if not val.valid:
            frozen_fb = format_integration_validation_feedback(
                val, previous_plan=plan_d
            )
    unlocked_fb = unlock_family_lock_feedback(frozen_fb) if frozen_fb else []
    return {
        "source": source,
        "a0": plan_d,
        "a0_eval": evaluate_plan(case, plan_d, und),
        "a0_valid": valid,
        "a0_codes": val_codes,
        "frozen_feedback": frozen_fb,
        "unlocked_feedback": unlocked_fb,
        "family_lock_present": any(FAMILY_LOCK_NEEDLE in x for x in frozen_fb),
    }


def isolated_retry(
    case: dict[str, Any],
    und: dict[str, Any],
    feedback: list[str],
    *,
    tag: str,
    model: str = "qwen2.5:7b",
) -> dict[str, Any]:
    enable_capture(tag)
    t0 = time.time()
    plan = build_integration_plan(
        case["user_prompt"],
        und,
        base_url=BASE_URL,
        model=model,
        retry_feedback=feedback,
    )
    elapsed = round(time.time() - t0, 3)
    d = plan.to_dict()
    ev = evaluate_plan(case, d, und)
    ev["elapsed_s"] = elapsed
    ev["label"] = "COUNTERFACTUAL" if "UNLOCK" in tag or "CF-" in tag else "OBSERVED"
    ev["plan"] = d
    ev["tag"] = tag
    return ev


def run_r1(*, n: int = R1_N, only: list[str] | None = None) -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    cases = [c for c in R1_CASES if not only or c in only]
    for cid in cases:
        case = load_case(cid)
        print(f"[r1] A0 {cid}", flush=True)
        und = load_understanding(case)
        a0 = _a0_and_feedback(case, und)
        if not a0["frozen_feedback"]:
            rows.append(
                {
                    "case_id": cid,
                    "skip_reason": "a0_valid_or_no_structural_feedback",
                    "a0": a0,
                    "frozen": [],
                    "unlocked": [],
                }
            )
            continue
        frozen_runs = []
        unlocked_runs = []
        for i in range(n):
            print(f"[r1] {cid} frozen {i+1}/{n}", flush=True)
            frozen_runs.append(
                isolated_retry(
                    case, und, a0["frozen_feedback"], tag=f"R1-{cid}-FROZEN-{i}"
                )
            )
            print(f"[r1] {cid} unlocked {i+1}/{n}", flush=True)
            unlocked_runs.append(
                isolated_retry(
                    case, und, a0["unlocked_feedback"], tag=f"R1-{cid}-UNLOCK-{i}"
                )
            )
        rows.append(
            {
                "case_id": cid,
                "label": "COUNTERFACTUAL vs OBSERVED retry instruction",
                "n": n,
                "a0": a0,
                "frozen": frozen_runs,
                "unlocked": unlocked_runs,
            }
        )
    payload = {"captured_at_utc": _utc(), "n": n, "rows": rows}
    (OUT / "r1_family_lock.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return payload


def counterfactual_strong(
    case: dict[str, Any],
    und: dict[str, Any],
    retry_log: list[dict[str, Any]],
    metadata: dict[str, Any],
    *,
    tag: str,
) -> dict[str, Any]:
    """Invoke existing strong loop as if fast status had been failed. COUNTERFACTUAL."""
    enable_capture(tag)
    decision = EscalationDecision(
        True,
        reason_code="recoverable_plan_validation_failure",
        evidence=["COUNTERFACTUAL_STRONG_ESCALATION", "fast_status_treated_as_failed"],
        from_model="qwen2.5:7b",
        to_model="qwen3:32b",
    )
    feedback = build_escalation_feedback(
        decision=decision, retry_log=retry_log, metadata=metadata
    )
    files = {str(k): v.copy() for k, v in case["files"].items()}
    t0 = time.time()
    strong = _run_integration_attempt_loop(
        case["user_prompt"],
        files,
        und,
        max_retries=2,
        base_url=BASE_URL,
        model="qwen3:32b",
        chat_json_fn=None,
        build_plan_fn=None,
        initial_feedback=feedback,
        path_label="COUNTERFACTUAL_STRONG_ESCALATION",
    )
    elapsed = round(time.time() - t0, 3)
    plan_d = strong.plan.to_dict() if strong.plan else None
    ev = evaluate_plan(case, plan_d, und)
    return {
        "label": "COUNTERFACTUAL_STRONG_ESCALATION",
        "tag": tag,
        "elapsed_s": elapsed,
        "pipeline_status": strong.status,
        "eval": ev,
        "plan": plan_d,
        "retry_log": strong.retry_log,
        "escalation_feedback": feedback,
    }


def _synthetic_failed_meta(retry_log: list[dict[str, Any]]) -> dict[str, Any]:
    codes = []
    for e in retry_log:
        if isinstance(e, dict):
            codes.extend(e.get("failure_codes") or [])
    return {
        "exhausted": True,
        "plan_validation_failure_count": sum(
            1
            for e in retry_log
            if isinstance(e, dict) and e.get("failure_stage") == "integration_plan_validation"
        ),
        "execution_failure_count": 0,
        "result_validation_failure_count": sum(
            1
            for e in retry_log
            if isinstance(e, dict) and e.get("failure_stage") == "integration_result_validation"
        ),
        "duplicate_plan_count": sum(
            1 for e in retry_log if isinstance(e, dict) and "repeated_plan" in (e.get("failure_codes") or [])
        ),
        "same_family_repeat_count": sum(
            1
            for e in retry_log
            if isinstance(e, dict) and "repeated_integration_family" in (e.get("failure_codes") or [])
        ),
        "repeated_final_contract_failure": "final_grain_contradiction" in codes,
        "validator_blocked_unsafe_plan": False,
    }


def load_step2_retry(case_id: str) -> dict[str, Any] | None:
    p = ROOT / "benchmark_results" / "multi" / "phase40_residual" / "case_results.jsonl"
    if not p.is_file():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("case_id") == case_id:
            return rec
    return None


def run_r3(*, only: list[str] | None = None) -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    ns = {"r40-B02": R3_N_B02, "r40-D03": R3_N_D, "r40-D04": R3_N_D}
    for cid in R3_CASES:
        if only and cid not in only:
            continue
        case = load_case(cid)
        und = load_understanding(case)
        step2 = load_step2_retry(cid) or {}
        retry_log = list(step2.get("retry_log") or [])
        # drop escalation entries; keep fast evidence
        retry_log = [
            e
            for e in retry_log
            if isinstance(e, dict) and e.get("attempt") != "escalation" and e.get("planner_path") != "strong"
        ]
        meta = _synthetic_failed_meta(retry_log)
        # confirm production would skip if cannot_plan
        skip = should_escalate_after_fast_path(
            status="cannot_plan",
            retry_log=retry_log,
            metadata=meta,
            strategy=PlannerModelStrategy(enable_escalation=True, strong_model="qwen3:32b"),
        )
        would_if_failed = should_escalate_after_fast_path(
            status="failed",
            retry_log=retry_log,
            metadata=meta,
            strategy=PlannerModelStrategy(enable_escalation=True, strong_model="qwen3:32b"),
        )
        n = ns[cid]
        runs = []
        for i in range(n):
            print(f"[r3] COUNTERFACTUAL 32B {cid} {i+1}/{n}", flush=True)
            runs.append(
                counterfactual_strong(
                    case, und, retry_log, meta, tag=f"R3-{cid}-CF32B-{i}"
                )
            )
        rows.append(
            {
                "case_id": cid,
                "production_step2_status": step2.get("pipeline_status"),
                "production_step2_path": step2.get("final_path"),
                "skip_cannot_plan": skip.to_dict(),
                "would_escalate_if_failed": would_if_failed.to_dict(),
                "n": n,
                "runs": runs,
            }
        )
    payload = {"captured_at_utc": _utc(), "rows": rows}
    (OUT / "r3_counterfactual_strong.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return payload


def run_frozen_repeats(case_id: str, n: int, *, prefix: str) -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    case = load_case(case_id)
    runs = []
    for i in range(n):
        tag = f"{prefix}-{case_id}-rep{i}"
        enable_capture(tag)
        print(f"[{prefix}] frozen pipeline {case_id} {i+1}/{n}", flush=True)
        t0 = time.time()
        sources = {str(k): v.copy() for k, v in case["files"].items()}
        und = load_understanding(case)
        result = run_integration_pipeline_semantic_experimental(
            case["user_prompt"],
            sources,
            und,
            config=production_config(),
            base_url=BASE_URL,
            request_id=tag,
            case_id=tag,
        )
        row = extract_row(result)
        row["rep"] = i
        row["total_elapsed_s"] = round(time.time() - t0, 3)
        row["tag"] = tag
        plan = row.get("final_plan")
        row["research_family"] = research_family(plan if isinstance(plan, dict) else None)
        row["grain"] = ((plan or {}) if isinstance(plan, dict) else {}).get("final_output_requirements", {})
        if isinstance(plan, dict):
            row["grain"] = (plan.get("final_output_requirements") or {}).get("grain")
        runs.append(row)
        print(
            f"[{prefix}] done {case_id} #{i} status={row.get('pipeline_status')} "
            f"path={row.get('final_path')} t={row['total_elapsed_s']}",
            flush=True,
        )
    payload = {"captured_at_utc": _utc(), "case_id": case_id, "n": n, "runs": runs}
    (OUT / f"{prefix}_{case_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return payload


def run_genuine_cannot_plan() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for cid in GENUINE_CP:
        case = load_case(cid)
        print(f"[cp] frozen {cid}", flush=True)
        enable_capture(f"CP-{cid}-FROZEN")
        sources = {str(k): v.copy() for k, v in case["files"].items()}
        t0 = time.time()
        und_obj = build_cross_file_understanding(
            list(sources.items()), base_url=BASE_URL, model="qwen2.5:7b", infer_relationships=True
        )
        und = und_obj.to_dict() if hasattr(und_obj, "to_dict") else dict(und_obj)
        frozen = run_integration_pipeline_semantic_experimental(
            case["user_prompt"],
            sources,
            und_obj,
            config=production_config(),
            base_url=BASE_URL,
            request_id=f"CP-{cid}-FROZEN",
            case_id=cid,
        )
        frozen_row = extract_row(frozen)
        frozen_row["elapsed_s"] = round(time.time() - t0, 3)
        retry_log = [
            e
            for e in (frozen_row.get("retry_log") or [])
            if isinstance(e, dict) and e.get("attempt") != "escalation"
        ]
        meta = _synthetic_failed_meta(retry_log)
        cf_runs = []
        for i in range(GENUINE_N):
            print(f"[cp] COUNTERFACTUAL 32B {cid} {i+1}/{GENUINE_N}", flush=True)
            cf_runs.append(
                counterfactual_strong(
                    case, und, retry_log, meta, tag=f"CP-{cid}-CF32B-{i}"
                )
            )
        rows.append(
            {
                "case_id": cid,
                "answerable": False,
                "frozen": frozen_row,
                "counterfactual_strong": cf_runs,
            }
        )
    payload = {"captured_at_utc": _utc(), "rows": rows}
    (OUT / "genuine_cannot_plan.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return payload


def run_offline() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    spec = state_machine_spec()
    skip = should_escalate_after_fast_path(
        status="cannot_plan",
        retry_log=[{"failure_codes": ["final_grain_contradiction"]}],
        metadata={"exhausted": True, "plan_validation_failure_count": 1},
        strategy=PlannerModelStrategy(enable_escalation=True),
    )
    failed = should_escalate_after_fast_path(
        status="failed",
        retry_log=[{"failure_codes": ["final_grain_contradiction"]}],
        metadata={"exhausted": True, "plan_validation_failure_count": 1},
        strategy=PlannerModelStrategy(enable_escalation=True),
    )
    sample_fb = [
        "This is a structural_contract_failure. "
        "Prefer repairing the previous plan: keep the same integration strategy family "
        "when the composition matches the user request; fix only contract violations "
        "(missing/renamed columns, aliases, step outputs, params shape). "
        "Do not invent keys or swap to an unrelated strategy. "
        "Semantic operation sequence can remain; downstream references must match "
        "declared intermediate schemas."
    ]
    unlocked = unlock_family_lock_feedback(sample_fb)
    payload = {
        "captured_at_utc": _utc(),
        "head": HEAD_EXPECTED,
        "shadow": bool(load_shadow_config().enabled),
        "state_machine": spec,
        "skip_cannot_plan": skip.to_dict(),
        "escalate_if_failed": failed.to_dict(),
        "unlock_demo": unlocked,
        "unlock_has_lock_needle": any(FAMILY_LOCK_NEEDLE in x for x in unlocked),
    }
    (OUT / "offline_state_machine.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (FIXTURES / "state_machine.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--mode",
        choices=["offline", "r1", "r3", "b02", "g01", "genuine", "all"],
        default="offline",
    )
    p.add_argument("--only", default="")
    args = p.parse_args()
    only = [x.strip() for x in args.only.split(",") if x.strip()] or None
    if args.mode in {"offline", "all"}:
        print(json.dumps({"offline": True, **{k: run_offline()[k] for k in ("skip_cannot_plan", "escalate_if_failed")}}, indent=2), flush=True)
    if args.mode in {"r1", "all"}:
        run_r1(only=only)
    if args.mode in {"b02", "all"}:
        run_frozen_repeats("r40-B02", B02_REPEAT_N, prefix="b02")
    if args.mode in {"g01", "all"}:
        run_frozen_repeats("r40-G01", G01_REPEAT_N, prefix="g01")
    if args.mode in {"r3", "all"}:
        run_r3(only=only)
    if args.mode in {"genuine", "all"}:
        run_genuine_cannot_plan()


if __name__ == "__main__":
    main()
