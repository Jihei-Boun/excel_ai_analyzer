"""Post-Phase-40 Step 3 — Planner cannot_plan / partition-grain root-cause ablation.

Research only. Calls frozen Candidate experimental path. Does not modify core/.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.integration_planner import _compact_understanding_for_prompt
from core.integrate.integration_result_validate import validate_integration_result
from core.integrate.planner_model_strategy import (
    PlannerModelStrategy,
    should_escalate_after_fast_path,
)
from core.integrate.relationship_infer import build_cross_file_understanding
from core.integrate.relationship_profile import build_file_profile, build_pairwise_observation
from core.integrate.semantic_escalation import (
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
    run_integration_pipeline_semantic_experimental,
)
from core.shadow.config import load_shadow_config
from tests.benchmark_multi.phase40_residual import (
    BASE_URL,
    HEAD_EXPECTED,
    extract_row,
    production_config,
)
from tests.benchmark_multi.phase40_residual_rootcause_plans import (
    TARGET_IDS,
    all_diagnostic_cases,
    case_by_id,
    reference_plans,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results" / "multi" / "phase40_residual_rootcause"
STEP2_JSONL = ROOT / "benchmark_results" / "multi" / "phase40_residual" / "case_results.jsonl"
CAPTURE_DIR = OUT / "planner_capture"
RESULTS_JSONL = OUT / "case_results.jsonl"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "phase40_residual_rootcause"

REQUIRED_EVIDENCE_KEYS = (
    "partition_column",
    "partition_values",
    "entity_grain",
    "metric",
    "join_identity",
)

CASE_EVIDENCE_SPEC: dict[str, dict[str, Any]] = {
    "r40-B02": {
        "partition_column": None,
        "partition_values": None,
        "entity_grain": "employee_id",
        "metric": "minutes",
        "join_identity": "employee_id",
    },
    "r40-D03": {
        "partition_column": "scenario",
        "partition_values": ["actual", "forecast"],
        "entity_grain": "line_id",
        "metric": "amount",
        "join_identity": "line_id",
    },
    "r40-D04": {
        "partition_column": "shift",
        "partition_values": ["day", "night"],
        "entity_grain": "sensor_id",
        "metric": "kwh",
        "join_identity": "sensor_id",
    },
    "r40-D01": {
        "partition_column": None,
        "partition_values": None,
        "entity_grain": "sku",
        "metric": "revenue",
        "join_identity": "sku",
    },
    "r40-F01": {
        "partition_column": "cabin",
        "partition_values": ["front", "rear"],
        "entity_grain": "car_id",
        "metric": "occupancy",
        "join_identity": "car_id",
    },
    "r40-F02": {
        "partition_column": "replicate",
        "partition_values": [1, 2],
        "entity_grain": "batch_id",
        "metric": "score",
        "join_identity": "batch_id",
    },
    "r40-F03": {
        "partition_column": "env",
        "partition_values": ["prod", "test"],
        "entity_grain": "host",
        "metric": "cpu",
        "join_identity": "host",
    },
    "r40-G01": {
        "partition_column": "region",
        "partition_values": ["west"],
        "entity_grain": "vendor_name",
        "metric": "q_sold",
        "join_identity": "vendor_id",
    },
    "r40-G03": {
        "partition_column": None,
        "partition_values": None,
        "entity_grain": ["store_id", "month"],
        "metric": "revenue",
        "join_identity": "sale_id",
    },
    "CTRL-D03-SPLIT": {
        "partition_column": None,
        "partition_values": None,
        "entity_grain": "line_id",
        "metric": "amount",
        "join_identity": "line_id",
    },
    "CTRL-D04-SPLIT": {
        "partition_column": None,
        "partition_values": None,
        "entity_grain": "sensor_id",
        "metric": "kwh",
        "join_identity": "sensor_id",
    },
    "CTRL-B02-SINGLE": {
        "partition_column": None,
        "partition_values": None,
        "entity_grain": "employee_id",
        "metric": "minutes",
        "join_identity": None,
    },
    "CTRL-B02-SIMPLE": {
        "partition_column": None,
        "partition_values": None,
        "entity_grain": "employee_id",
        "metric": "minutes",
        "join_identity": "employee_id",
    },
    "CTRL-D03-NEUTRAL": {
        "partition_column": "scenario",
        "partition_values": ["alpha", "beta"],
        "entity_grain": "line_id",
        "metric": "amount",
        "join_identity": "line_id",
    },
    "CTRL-D03-METRIC": {
        "partition_column": "scenario",
        "partition_values": ["actual", "forecast"],
        "entity_grain": "line_id",
        "metric": "qty",
        "join_identity": "line_id",
    },
}


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def deterministic_understanding(files: dict[str, pd.DataFrame]) -> dict[str, Any]:
    names = list(files.keys())
    profiles = [build_file_profile(n, files[n]).to_dict() for n in names]
    pairs = []
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            pairs.append(
                build_pairwise_observation(left, files[left], right, files[right]).to_dict()
            )
    return {
        "file_profiles": profiles,
        "pairwise_observations": pairs,
        "relationships": [],
    }


def _column_index(und: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in und.get("file_profiles") or []:
        obs = p.get("observations") or {}
        for c in obs.get("columns") or []:
            if isinstance(c, dict) and c.get("name"):
                out[str(c["name"])] = c
    return out


def _presence(needed: Any, columns: dict[str, dict[str, Any]], files: dict[str, pd.DataFrame]) -> str:
    if needed is None:
        return "n/a"
    names = needed if isinstance(needed, list) else [needed]
    col_ok = all(str(n) in columns for n in names)
    if col_ok:
        return "present"
    # also check raw frames
    raw_ok = all(any(str(n) in df.columns for df in files.values()) for n in names)
    return "present" if raw_ok else "missing"


def _values_presence(values: Any, columns: dict[str, dict[str, Any]], part_col: Any) -> str:
    if not values:
        return "n/a"
    if not part_col:
        return "n/a"
    col = columns.get(str(part_col)) or {}
    samples = [str(x) for x in (col.get("sample_values") or [])]
    want = [str(v) for v in values]
    if all(w in samples for w in want):
        return "present"
    if any(w in samples for w in want):
        return "partial"
    return "missing"


def evidence_row(
    case: dict[str, Any],
    *,
    planner_und: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = CASE_EVIDENCE_SPEC.get(case["case_id"]) or {}
    files = case["files"]
    raw_und = deterministic_understanding(files)
    raw_cols = _column_index(raw_und)
    compact = _compact_understanding_for_prompt(raw_und)
    compact_cols = _column_index(compact)
    live_cols = _column_index(planner_und) if planner_und else compact_cols
    table = {}
    for key in REQUIRED_EVIDENCE_KEYS:
        needed = spec.get(key)
        if key == "partition_values":
            table[key] = {
                "raw_source": _values_presence(needed, raw_cols, spec.get("partition_column")),
                "understanding": _values_presence(
                    needed, compact_cols, spec.get("partition_column")
                ),
                "planner_input": _values_presence(
                    needed, live_cols, spec.get("partition_column")
                ),
            }
        else:
            table[key] = {
                "raw_source": _presence(needed, raw_cols, files),
                "understanding": _presence(needed, compact_cols, files),
                "planner_input": _presence(needed, live_cols, files),
            }
    h1 = "REJECTED"
    h2 = "REJECTED"
    for key, cell in table.items():
        if cell["raw_source"] == "missing" and (spec.get(key) not in (None, [], ())):
            h1 = "SUPPORTED"
        if cell["raw_source"] in {"present", "n/a"} and cell["planner_input"] == "missing":
            h2 = "SUPPORTED"
    return {
        "case_id": case["case_id"],
        "table": table,
        "h1_input_missing": h1,
        "h2_understanding_distortion": h2,
        "compact_column_names": sorted(compact_cols.keys()),
        "sample_preview": {
            name: (col.get("sample_values") or [])[:4]
            for name, col in compact_cols.items()
        },
    }


def _manual_semantic_ok(case_id: str, frame: pd.DataFrame | None) -> dict[str, Any]:
    if frame is None:
        return {"ok": False, "reason": "no_frame"}
    cols = {str(c) for c in frame.columns}
    rows = frame.to_dict(orient="records")

    def has(*names: str) -> bool:
        return all(n in cols for n in names)

    checks: dict[str, Any] = {"row_count": int(len(frame)), "columns": sorted(cols)}
    ok = False
    reason = "unmatched_case"
    if case_id in {"r40-B02", "CTRL-B02-SINGLE", "CTRL-B02-SIMPLE"}:
        ok = has("employee_id") and any("minute" in c.lower() or c == "total_minutes" for c in cols)
        if "team" in cols and len(frame) <= 2:
            ok = False
            reason = "team_grain_too_coarse"
        else:
            reason = "employee grain present"
            # E1=60, E2=35, E3=50
            by = {str(r.get("employee_id")): r for r in rows}
            metric_col = next(
                (c for c in frame.columns if c != "employee_id"),
                None,
            )
            if metric_col is not None and {"E1", "E2", "E3"} <= set(by):
                vals = {k: float(by[k][metric_col]) for k in ("E1", "E2", "E3")}
                ok = vals == {"E1": 60.0, "E2": 35.0, "E3": 50.0}
                reason = f"employee totals {vals}"
    elif case_id in {"r40-D03", "CTRL-D03-SPLIT"}:
        ok = has("line_id") and any("actual" in c.lower() for c in cols) and any(
            "forecast" in c.lower() for c in cols
        )
        reason = "two scenario columns"
    elif case_id == "CTRL-D03-NEUTRAL":
        ok = has("line_id") and any("alpha" in c.lower() for c in cols) and any(
            "beta" in c.lower() for c in cols
        )
        reason = "two partition columns"
    elif case_id == "CTRL-D03-METRIC":
        ok = has("line_id") and any("actual" in c.lower() for c in cols) and any(
            "forecast" in c.lower() for c in cols
        )
        reason = "two qty columns"
    elif case_id in {"r40-D04", "CTRL-D04-SPLIT"}:
        ok = has("sensor_id") and any("day" in c.lower() for c in cols) and any(
            "night" in c.lower() for c in cols
        )
        reason = "day and night kwh"
    elif case_id == "r40-D01":
        ok = has("sku") and sum(1 for c in cols if "revenue" in c.lower() or "2023" in c or "2024" in c) >= 2
        reason = "both year revenues"
    elif case_id == "r40-F01":
        ok = has("car_id") and any("front" in c.lower() for c in cols) and any(
            "rear" in c.lower() for c in cols
        )
        reason = "front/rear occupancy"
    elif case_id == "r40-F02":
        ok = has("batch_id") and sum(1 for c in cols if "score" in c.lower() or "replicate" in c.lower()) >= 2
        reason = "two replicate scores"
    elif case_id == "r40-F03":
        ok = has("host") and any("prod" in c.lower() for c in cols) and any(
            "test" in c.lower() for c in cols
        )
        reason = "prod/test cpu"
    elif case_id == "r40-G01":
        ok = has("vendor_name") and any("sold" in c.lower() or "qty" in c.lower() for c in cols)
        by = {str(r.get("vendor_name")): r for r in rows}
        if "Pike" in by:
            metric_col = next((c for c in frame.columns if c != "vendor_name"), None)
            if metric_col is not None:
                ok = float(by["Pike"][metric_col]) == 10.0 and "Harbor" not in by
                reason = f"west totals; Harbor excluded; pike={by.get('Pike')}"
    elif case_id == "r40-G03":
        ok = has("store_id", "month") and any("revenue" in c.lower() for c in cols)
        reason = "store_id+month grain"
        if ok and len(frame) == 3:
            reason = "store_id+month grain, 3 groups"
    checks["ok"] = bool(ok)
    checks["reason"] = reason
    return checks


def evaluate_reference_plan(case: dict[str, Any], plan_dict: dict[str, Any]) -> dict[str, Any]:
    files = {str(k): v.copy() for k, v in case["files"].items()}
    und = deterministic_understanding(files)
    plan = integration_plan_from_dict(plan_dict)
    val = validate_integration_plan(und, plan, user_prompt=case["user_prompt"], frames=files)
    row: dict[str, Any] = {
        "case_id": case["case_id"],
        "dsl_expressible": True,
        "validator_valid": bool(val.valid),
        "validator_codes": [e.code for e in val.errors],
        "validator_warnings": [w.code for w in val.warnings],
        "executor_success": None,
        "result_validator_valid": None,
        "result_validator_codes": [],
        "manual": {"ok": False, "reason": "not_executed"},
        "columns": [],
        "n_rows": None,
    }
    if not val.valid:
        row["dsl_expressible"] = "PARTIAL"
        return row
    exe = execute_integration_plan(files, plan, val)
    row["executor_success"] = bool(exe.success)
    if not exe.success:
        row["executor_error"] = getattr(getattr(exe, "error", None), "code", None)
        return row
    res = validate_integration_result(plan, exe, plan_validation=val)
    row["result_validator_valid"] = bool(res.valid)
    row["result_validator_codes"] = [e.code for e in res.errors]
    fo = exe.final_output
    if isinstance(fo, pd.DataFrame):
        row["columns"] = [str(c) for c in fo.columns]
        row["n_rows"] = int(len(fo))
        row["sample"] = fo.head(8).to_dict(orient="records")
        row["manual"] = _manual_semantic_ok(case["case_id"], fo)
    return row


def evaluate_all_reference_plans() -> list[dict[str, Any]]:
    plans = reference_plans()
    rows = []
    for case in all_diagnostic_cases():
        plan = plans.get(case["case_id"])
        if plan is None:
            continue
        rows.append(evaluate_reference_plan(case, plan))
    return rows


def load_step2_rows() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not STEP2_JSONL.is_file():
        return out
    for line in STEP2_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        cid = rec.get("case_id")
        if cid:
            out[str(cid)] = rec
    return out


def reconstruct_step2_attempts(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """Attempt-aware reconstruction from Step 2 compact traces (no raw LLM text)."""
    attempts: list[dict[str, Any]] = []
    retry = list(rec.get("retry_log") or [])
    parent = None
    seq = 0
    for e in retry:
        if not isinstance(e, dict):
            continue
        if e.get("attempt") == "escalation":
            attempts.append(
                {
                    "attempt_id": f"{rec.get('case_id')}:ESC",
                    "parent_attempt_id": parent,
                    "model": e.get("to_model") or rec.get("final_model"),
                    "reason": e.get("failure_codes") or e.get("failure_stage"),
                    "failure_stage": e.get("failure_stage"),
                    "failure_codes": e.get("failure_codes"),
                    "raw_output": None,
                    "parsed_plan": None,
                    "validation": None,
                    "note": "escalation_boundary",
                }
            )
            parent = attempts[-1]["attempt_id"]
            continue
        aid = f"{rec.get('case_id')}:A{seq}"
        attempts.append(
            {
                "attempt_id": aid,
                "parent_attempt_id": parent,
                "model": "qwen2.5:7b" if e.get("planner_path") in {None, "fast"} else rec.get("final_model"),
                "planner_path": e.get("planner_path") or "fast",
                "failure_stage": e.get("failure_stage"),
                "failure_codes": e.get("failure_codes"),
                "selected_ops": e.get("selected_ops"),
                "operation_family": e.get("operation_family"),
                "evidence_summary": e.get("evidence_summary"),
                "expected_schema_by_step": e.get("expected_schema_by_step"),
                "retry_mode": e.get("retry_mode"),
                "raw_output": None,
                "parsed_plan_body": "NOT_STORED_IN_STEP2",
            }
        )
        parent = aid
        seq += 1
    attempts.append(
        {
            "attempt_id": f"{rec.get('case_id')}:FINAL",
            "parent_attempt_id": parent,
            "model": rec.get("final_model"),
            "pipeline_status": rec.get("pipeline_status"),
            "final_path": rec.get("final_path"),
            "parsed_plan": rec.get("final_plan"),
            "reason": (rec.get("final_plan") or {}).get("reason"),
        }
    )
    return attempts


def escalation_would_skip_cannot_plan() -> dict[str, Any]:
    decision = should_escalate_after_fast_path(
        status="cannot_plan",
        retry_log=[
            {
                "failure_codes": ["final_grain_contradiction"],
                "failure_stage": "integration_plan_validation",
            }
        ],
        metadata={"exhausted": True, "plan_validation_failure_count": 1},
        strategy=PlannerModelStrategy(
            fast_model="qwen2.5:7b",
            strong_model="qwen3:32b",
            enable_escalation=True,
        ),
    )
    return decision.to_dict()


def _load_capture_records(case_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not CAPTURE_DIR.is_dir():
        return rows
    for path in sorted(CAPTURE_DIR.glob("planner_invocations_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("case_id") == case_id or rec.get("request_id") == case_id:
                rows.append(rec)
    return rows


def reconstruct_live_attempts(case_id: str, pipeline_row: dict[str, Any]) -> list[dict[str, Any]]:
    caps = _load_capture_records(case_id)
    out: list[dict[str, Any]] = []
    for i, rec in enumerate(caps):
        parsed = rec.get("parsed_plan")
        out.append(
            {
                "attempt_id": rec.get("planner_invocation_id") or f"{case_id}:cap{i}",
                "parent_attempt_id": out[-1]["attempt_id"] if out else None,
                "planner_type": rec.get("planner_type"),
                "model": rec.get("model"),
                "parse_attempt": rec.get("parse_attempt"),
                "parse_ok": rec.get("parse_ok"),
                "parse_error": rec.get("parse_error"),
                "backend_error": rec.get("backend_error"),
                "raw_outcome": rec.get("raw_outcome"),
                "cannot_plan_subtype": rec.get("cannot_plan_subtype"),
                "retry_feedback": rec.get("retry_feedback"),
                "user_prompt_head": str(
                    ((rec.get("structured_input") or {}).get("user_prompt") or "")
                )[:4000],
                "raw_model_response_text": rec.get("raw_model_response_text"),
                "parsed_plan": parsed,
                "latency_s": rec.get("latency_s"),
            }
        )
    out.append(
        {
            "attempt_id": f"{case_id}:PIPELINE_FINAL",
            "parent_attempt_id": out[-1]["attempt_id"] if out else None,
            "pipeline_status": pipeline_row.get("pipeline_status"),
            "final_path": pipeline_row.get("final_path"),
            "retry_log": pipeline_row.get("retry_log"),
            "final_plan": pipeline_row.get("final_plan"),
        }
    )
    return out


def classify_retry(retry_log: list[dict[str, Any]], captures: list[dict[str, Any]]) -> str:
    if not retry_log:
        return "NO_RETRY"
    collapsed = False
    for cap in captures:
        plan = cap.get("parsed_plan") or {}
        if isinstance(plan, dict) and plan.get("status") == "cannot_plan":
            collapsed = True
        if cap.get("raw_outcome") in {"FORMAT_FAILURE", "DECLARED_CANNOT_PLAN"} and cap.get(
            "retry_feedback"
        ):
            collapsed = collapsed or (
                str((plan or {}).get("status")) == "cannot_plan"
                or cap.get("cannot_plan_subtype") in {"FORMAT_EXHAUSTION", "EXPLICIT_MODEL_CANNOT_PLAN"}
            )
    codes = []
    for e in retry_log:
        if isinstance(e, dict):
            codes.extend(e.get("failure_codes") or [])
    if collapsed and any(
        c in {"final_grain_contradiction", "nonexistent_column", "required_field_not_materializable"}
        for c in codes
    ):
        return "RETRY_COLLAPSED_TO_CANNOT_PLAN"
    if any(e.get("failure_codes") == ["repeated_plan", "repeated_integration_family"] or
           (isinstance(e.get("failure_codes"), list) and "repeated_plan" in (e.get("failure_codes") or []))
           for e in retry_log if isinstance(e, dict)):
        return "FEEDBACK_SUFFICIENT_BUT_IGNORED"
    return "UNKNOWN"


def enable_planner_capture(case_id: str) -> None:
    os.environ["MULTI_PLANNER_CAPTURE_ENABLED"] = "true"
    os.environ["MULTI_PLANNER_CAPTURE_DIR"] = str(CAPTURE_DIR)
    os.environ["MULTI_PLANNER_CAPTURE_REQUEST_ID"] = case_id
    os.environ["MULTI_PLANNER_CAPTURE_CASE_ID"] = case_id


def run_one_live(case: dict[str, Any]) -> dict[str, Any]:
    sources = {str(k): v.copy() for k, v in case["files"].items()}
    enable_planner_capture(case["case_id"])
    t0 = time.time()
    row: dict[str, Any] = {
        "case_id": case["case_id"],
        "request_id": case["request_id"],
        "category": case["category"],
        "user_prompt": case["user_prompt"],
        "source_files": case["source_files"],
        "manual_expected_outcome": case["manual_expected_outcome"],
        "manual_semantic_requirements": case["manual_semantic_requirements"],
        "answerability": case["answerability"],
        "captured_at_utc": _utc(),
        "verifier_model": SEMANTIC_VERIFIER_MODEL,
        "verifier_variant": SEMANTIC_VERIFIER_VARIANT,
        "shadow_enabled_at_run": bool(load_shadow_config().enabled),
        "error": None,
        "error_family": None,
    }
    try:
        t_und = time.time()
        understanding = build_cross_file_understanding(
            list(sources.items()),
            base_url=BASE_URL,
            model="qwen2.5:7b",
            infer_relationships=True,
        )
        und_dict = understanding.to_dict() if hasattr(understanding, "to_dict") else dict(understanding)
        row["understanding_elapsed_s"] = round(time.time() - t_und, 3)
        row["understanding"] = und_dict
        row["compact_understanding"] = _compact_understanding_for_prompt(und_dict)
        t_pipe = time.time()
        result = run_integration_pipeline_semantic_experimental(
            case["user_prompt"],
            sources,
            understanding,
            config=production_config(),
            base_url=BASE_URL,
            request_id=case["request_id"],
            case_id=case["case_id"],
        )
        row["pipeline_elapsed_s"] = round(time.time() - t_pipe, 3)
        row.update(extract_row(result))
        row["live_attempts"] = reconstruct_live_attempts(case["case_id"], row)
        row["retry_classification"] = classify_retry(
            list(row.get("retry_log") or []),
            _load_capture_records(case["case_id"]),
        )
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["error_family"] = type(exc).__name__
        row["error_traceback_tail"] = traceback.format_exc()[-2000:]
    row["total_elapsed_s"] = round(time.time() - t0, 3)
    return row


def _done_ids(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.is_file():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        cid = rec.get("case_id")
        if isinstance(cid, str):
            done.add(cid)
    return done


def run_offline() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    ref_rows = evaluate_all_reference_plans()
    evidence = [evidence_row(c) for c in all_diagnostic_cases()]
    step2 = load_step2_rows()
    step2_attempts = {
        cid: reconstruct_step2_attempts(rec)
        for cid, rec in step2.items()
        if cid in TARGET_IDS
    }
    skip_cp = escalation_would_skip_cannot_plan()
    payload = {
        "captured_at_utc": _utc(),
        "head_expected": HEAD_EXPECTED,
        "shadow_enabled": bool(load_shadow_config().enabled),
        "escalation_skips_cannot_plan": skip_cp,
        "reference_plans": ref_rows,
        "evidence": evidence,
        "step2_attempts": step2_attempts,
        "step2_retry_logs": {
            cid: (step2[cid].get("retry_log") if cid in step2 else None)
            for cid in TARGET_IDS
        },
    }
    (OUT / "offline_ablation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (FIXTURES / "offline_ablation_summary.json").write_text(
        json.dumps(
            {
                "n_reference": len(ref_rows),
                "all_validator_valid": all(r.get("validator_valid") for r in ref_rows),
                "all_executor_success": all(r.get("executor_success") for r in ref_rows),
                "all_manual_ok": all((r.get("manual") or {}).get("ok") for r in ref_rows),
                "escalation_skips_cannot_plan": skip_cp.get("should_escalate") is False
                and skip_cp.get("reason_code") == "skip_cannot_plan",
                "h1_any_supported": any(e["h1_input_missing"] == "SUPPORTED" for e in evidence),
                "h2_any_supported": any(
                    e["h2_understanding_distortion"] == "SUPPORTED" for e in evidence
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return payload


def run_live(*, only: list[str] | None = None, resume: bool = True) -> list[dict[str, Any]]:
    OUT.mkdir(parents=True, exist_ok=True)
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    cases = all_diagnostic_cases()
    if only:
        want = set(only)
        cases = [c for c in cases if c["case_id"] in want]
    done = _done_ids(RESULTS_JSONL) if resume else set()
    rows: list[dict[str, Any]] = []
    if RESULTS_JSONL.is_file() and resume:
        for line in RESULTS_JSONL.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if not only or rec.get("case_id") in (only or []):
                    rows.append(rec)
    for case in cases:
        if case["case_id"] in done:
            print(f"[r40rc] skip {case['case_id']}", flush=True)
            continue
        print(f"[r40rc] start {case['case_id']} {case['category']}", flush=True)
        rec = run_one_live(case)
        slim = dict(rec)
        # keep understanding on disk separately; jsonl stays large but usable
        und_path = OUT / "understanding" / f"{case['case_id']}.json"
        und_path.parent.mkdir(parents=True, exist_ok=True)
        und_path.write_text(
            json.dumps(
                {
                    "understanding": rec.get("understanding"),
                    "compact_understanding": rec.get("compact_understanding"),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        with RESULTS_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(slim, ensure_ascii=False, default=str) + "\n")
        rows.append(rec)
        print(
            f"[r40rc] done {case['case_id']} status={rec.get('pipeline_status')} "
            f"path={rec.get('final_path')} t={rec.get('total_elapsed_s')} "
            f"retry={rec.get('retry_classification')}",
            flush=True,
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["offline", "live", "both"], default="offline")
    parser.add_argument("--only", default="")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    only = [x.strip() for x in args.only.split(",") if x.strip()] or None
    if args.mode in {"offline", "both"}:
        payload = run_offline()
        print(
            json.dumps(
                {
                    "offline": True,
                    "n_ref": len(payload["reference_plans"]),
                    "validator": [r["case_id"] + ":" + str(r["validator_valid"]) for r in payload["reference_plans"]],
                },
                indent=2,
            ),
            flush=True,
        )
    if args.mode in {"live", "both"}:
        run_live(only=only, resume=not args.no_resume)


if __name__ == "__main__":
    main()
