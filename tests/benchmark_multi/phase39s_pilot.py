"""Phase 39S — Targeted C/D family Shadow coverage completion.

Observation only. Frozen 39R architecture. Identity-based collection.
Does not repeat the 15-request 39R set. Does not reuse 39R labels.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from core.routing.route_multi import route_multi_prompt
from core.shadow.isolation import (
    bind_records_by_request_id,
    capture_integrity_report,
    lineage_integrity_report,
    select_record_for_request,
)
from core.shadow.worker import (
    get_inflight_for_tests,
    reload_config_for_tests,
    reset_shadow_worker_for_tests,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase39s"
TEL = OUT / "telemetry"
DATA = OUT / "datasets"
CAPTURE = OUT / "verifier_captures"

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = "qwen2.5:7b"
MATERIALIZATION = "final_schema_expr_partition"

# Frozen 39R budgets. Do not inflate.
CALLER_WAIT_SEC = 1800.0
END_DRAIN_SEC = 1800.0

STAGE_RANK = {
    "FINALIZED": 90,
    "SEMANTIC_ESCALATION": 80,
    "VERIFIER_COMPLETED": 70,
    "VERIFIER_REACHED": 60,
    "STRONG_ATTEMPT_CREATED": 50,
    "FAILURE_ESCALATION": 40,
    "RESULT_VALIDATION_FAILED": 35,
    "EXECUTION_FAILED": 30,
    "VALIDATION_FAILED": 25,
    "PLAN_CREATED": 20,
    "CANNOT_PLAN": 15,
    "PLANNER_FAILED": 10,
    "OPERATIONAL_TIMEOUT": 8,
    "PLANNER_NOT_REACHED": 5,
    "OTHER": 1,
}


def _cases() -> list[dict[str, Any]]:
    """Frozen 4 C + 4 D. New IDs, new domains, conceptual families only."""
    cases: list[dict[str, Any]] = []

    # ----- C fake-dual / collapse pressure -----
    cases.append({
        "case_id": "P39S-C01",
        "family": "C",
        "request_id": "p39s-c-01",
        "structural_shape": "C1_two_files_same_metric_no_side_discriminator",
        "prompt": (
            "Compare marsh inlet versus marsh outlet liters for each reed_id "
            "and keep both side totals visible."
        ),
        "files": {
            "marsh_volume.xlsx": pd.DataFrame({
                "reed_id": ["RD1", "RD2"],
                "liters": [18, 11],
            }),
            "reeds.xlsx": pd.DataFrame({
                "reed_id": ["RD1", "RD2"],
                "bank": ["east", "west"],
            }),
        },
        "review_focus": (
            "Two files contribute one volume metric. There is no inlet/outlet "
            "partition. Dual aliases of one aggregate are collapsed evidence."
        ),
        "manual_correctness_criteria": (
            "NO if the attempt exposes two comparison sides from the same "
            "liters expression/population. YES only if independently grounded "
            "inlet vs outlet evidence exists (it does not in these files)."
        ),
        "note": "C1: same metric, no side discriminator.",
    })
    cases.append({
        "case_id": "P39S-C02",
        "family": "C",
        "request_id": "p39s-c-02",
        "structural_shape": "C2_requested_sides_without_distinct_evidence",
        "prompt": (
            "Compare quarry face versus quarry back tonnes by bench_id and "
            "keep both face and back totals visible."
        ),
        "files": {
            "quarry_tonnes.xlsx": pd.DataFrame({
                "bench_id": ["BN1", "BN2", "BN1", "BN2"],
                "tonnes": [20, 14, 9, 7],
            }),
            "benches.xlsx": pd.DataFrame({
                "bench_id": ["BN1", "BN2"],
                "ledge": ["L1", "L2"],
            }),
        },
        "review_focus": (
            "Requested face/back sides have no distinct column or filter. "
            "A collapsing aggregate that removes side distinction is fake dual."
        ),
        "manual_correctness_criteria": (
            "NO if one tonnes population is aggregated and relabeled as both "
            "face and back. Do not treat later child success as parent correctness."
        ),
        "note": "C2: collapse removes a distinction the files cannot support.",
    })
    cases.append({
        "case_id": "P39S-C03",
        "family": "C",
        "request_id": "p39s-c-03",
        "structural_shape": "C3_identical_expression_alias_pair",
        "prompt": (
            "Compare barn ration Alpha versus barn ration Beta kilograms by "
            "stall_id and keep both ration totals visible."
        ),
        "files": {
            "barn_feed.xlsx": pd.DataFrame({
                "stall_id": ["ST1", "ST2"],
                "kg": [6.0, 4.5],
            }),
            "stalls.xlsx": pd.DataFrame({
                "stall_id": ["ST1", "ST2"],
                "wing": ["N", "S"],
            }),
        },
        "review_focus": (
            "FD1-like pressure: two aliases of the same kg expression over the "
            "same row population are not two rations."
        ),
        "manual_correctness_criteria": (
            "NO if Alpha/Beta columns share identical expression + partition "
            "ancestry. Different aliases do not prove independence."
        ),
        "note": "C3: identical-signature alias pair pressure.",
    })
    cases.append({
        "case_id": "P39S-C04",
        "family": "C",
        "request_id": "p39s-c-04",
        "structural_shape": "C4_entity_total_duplicated_as_two_sides",
        "prompt": (
            "Show weekday_crates and weekend_crates for each dock_id. Keep "
            "both weekday and weekend totals visible; do not collapse to one total."
        ),
        "files": {
            "dock_crates.xlsx": pd.DataFrame({
                "dock_id": ["DK1", "DK2"],
                "crates": [15, 8],
            }),
            "docks.xlsx": pd.DataFrame({
                "dock_id": ["DK1", "DK2"],
                "pier": ["P1", "P2"],
            }),
        },
        "review_focus": (
            "Entity-level comparison with no weekday/weekend partition. "
            "One total duplicated under two names is fake dual."
        ),
        "manual_correctness_criteria": (
            "NO if a single crates total is duplicated as weekday and weekend. "
            "There is no independent period ancestry in the files."
        ),
        "note": "C4: one entity total duplicated as two sides.",
    })

    # ----- D same-origin independently partitioned valid -----
    cases.append({
        "case_id": "P39S-D01",
        "family": "D",
        "request_id": "p39s-d-01",
        "structural_shape": "D1_same_metric_split_by_explicit_category",
        "prompt": (
            "Using the well readings, compare day D1 versus day D2 liters by "
            "well_id and keep both day totals visible."
        ),
        "files": {
            "well_readings.xlsx": pd.DataFrame({
                "well_id": ["WL1", "WL1", "WL2", "WL2"],
                "day": ["D1", "D2", "D1", "D2"],
                "liters": [12, 15, 9, 11],
            }),
            "wells.xlsx": pd.DataFrame({
                "well_id": ["WL1", "WL2"],
                "field": ["F1", "F2"],
            }),
        },
        "review_focus": (
            "Same origin liters, independently split by explicit day category. "
            "Same origin does not imply same evidence."
        ),
        "manual_correctness_criteria": (
            "YES if D1 and D2 retain distinct partition/filter ancestry and "
            "both day totals remain visible by well_id. Do not FAIL solely "
            "because both sides originate from well_readings.xlsx."
        ),
        "note": "D1: explicit day category partitions.",
    })
    cases.append({
        "case_id": "P39S-D02",
        "family": "D",
        "request_id": "p39s-d-02",
        "structural_shape": "D2_same_origin_independently_file_partitioned",
        "prompt": (
            "Show kiln_dawn_units and kiln_dusk_units for each kiln_id after "
            "combining the two independently partitioned kiln shift extracts."
        ),
        "files": {
            "kiln_dawn.xlsx": pd.DataFrame({
                "kiln_id": ["KN1", "KN2"],
                "units": [10, 8],
            }),
            "kiln_dusk.xlsx": pd.DataFrame({
                "kiln_id": ["KN1", "KN2"],
                "units": [12, 9],
            }),
        },
        "review_focus": (
            "Same-origin units metric already independently partitioned into "
            "dawn vs dusk files. Genuine comparison if both sides survive."
        ),
        "manual_correctness_criteria": (
            "YES if dawn and dusk units are independently sourced/aggregated "
            "and shown side by side. NO if union+one aggregate is duplicated "
            "under both names."
        ),
        "note": "D2: pre-split independent file partitions of one metric.",
    })
    cases.append({
        "case_id": "P39S-D03",
        "family": "D",
        "request_id": "p39s-d-03",
        "structural_shape": "D3_same_origin_period_group_distinct_ancestry",
        "prompt": (
            "Using buoy signals, compare season wet versus season dry pulses "
            "by buoy_id and keep both season totals visible."
        ),
        "files": {
            "buoy_signal.xlsx": pd.DataFrame({
                "buoy_id": ["BY1", "BY1", "BY2", "BY2"],
                "season": ["wet", "dry", "wet", "dry"],
                "pulses": [30, 18, 22, 16],
            }),
            "buoys.xlsx": pd.DataFrame({
                "buoy_id": ["BY1", "BY2"],
                "channel": ["C1", "C2"],
            }),
        },
        "review_focus": (
            "Same-origin pulses partitioned by season with distinct ancestry."
        ),
        "manual_correctness_criteria": (
            "YES if wet and dry filters/aggregates are independent and both "
            "columns remain. Shared buoy_signal.xlsx origin is acceptable."
        ),
        "note": "D3: season group partitions.",
    })
    cases.append({
        "case_id": "P39S-D04",
        "family": "D",
        "request_id": "p39s-d-04",
        "structural_shape": "D4_non_overlapping_week_file_partitions",
        "prompt": (
            "Show loom_w1_meters and loom_w2_meters for each loom_id after "
            "combining the two non-overlapping week extracts."
        ),
        "files": {
            "loom_w1.xlsx": pd.DataFrame({
                "loom_id": ["LM1", "LM2"],
                "meters": [40, 33],
            }),
            "loom_w2.xlsx": pd.DataFrame({
                "loom_id": ["LM1", "LM2"],
                "meters": [38, 36],
            }),
        },
        "review_focus": (
            "Same-origin meters independently aggregated under non-overlapping "
            "week file partitions."
        ),
        "manual_correctness_criteria": (
            "YES if W1 and W2 meters keep independent file/partition ancestry. "
            "NO if a single combined total is duplicated as both weeks."
        ),
        "note": "D4: non-overlapping week partitions as two files.",
    })
    return cases


COMPLETION_RULE = {
    "name": "identity_bound_finalization",
    "text": (
        "A case is complete enough for review when a telemetry record exists "
        "whose request_id equals the submitted request_id. Caller wait budget "
        "is 1800s (frozen 39R budget, not inflated). If the budget expires "
        "without that identity, caller_timeout=true. After the official set, "
        "drain inflight workers and re-bind any late records by request_id. "
        "Late identity-bound telemetry is semantically review-eligible; "
        "caller_timeout remains the operational status. Never use new_recs[-1] "
        "or stamp a foreign request_id onto a record."
    ),
    "caller_wait_sec": CALLER_WAIT_SEC,
    "end_drain_sec": END_DRAIN_SEC,
    "associate_by": "request_id",
    "never": ["new_recs[-1]", "zip(submission_order, completions)", "stamp_loop_rid"],
}


def _load_all_telemetry() -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    for p in sorted(TEL.glob("shadow_*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs


def _load_all_captures() -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    for p in sorted(CAPTURE.glob("verifier_invocations_*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs


def await_identity_record(
    request_id: str, *, timeout_s: float = CALLER_WAIT_SEC
) -> tuple[dict[str, Any] | None, bool]:
    t0 = time.time()
    last_log = 0.0
    while time.time() - t0 < timeout_s:
        rec = select_record_for_request(_load_all_telemetry(), request_id)
        if rec is not None:
            return rec, False
        n = get_inflight_for_tests()
        now = time.time()
        if now - last_log > 30:
            print(
                f"  …waiting identity={request_id} inflight={n} "
                f"elapsed={round(now - t0, 1)}s",
                flush=True,
            )
            last_log = now
        if n <= 0 and (now - t0) > 12.0:
            time.sleep(1.0)
            rec = select_record_for_request(_load_all_telemetry(), request_id)
            return rec, rec is None
        time.sleep(1.0)
    rec = select_record_for_request(_load_all_telemetry(), request_id)
    return rec, rec is None


def drain_inflight(*, timeout_s: float = END_DRAIN_SEC) -> dict[str, Any]:
    t0 = time.time()
    while get_inflight_for_tests() > 0 and time.time() - t0 < timeout_s:
        time.sleep(0.5)
    time.sleep(0.8)
    return {
        "elapsed_s": round(time.time() - t0, 3),
        "inflight_remaining": get_inflight_for_tests(),
        "timed_out": get_inflight_for_tests() > 0,
    }


def _ops_summary(plan: Any) -> list[str] | None:
    if not isinstance(plan, dict):
        return None
    ops = []
    for s in plan.get("steps") or []:
        if isinstance(s, dict) and s.get("op"):
            ops.append(str(s["op"]))
    return ops or None


def classify_stages(shadow: dict[str, Any], rec: dict[str, Any] | None) -> dict[str, Any]:
    """Deterministic operational stage timeline + deepest_stage."""
    timeline: list[str] = []
    if rec is None:
        return {
            "timeline": ["PLANNER_NOT_REACHED"],
            "deepest_stage": "PLANNER_NOT_REACHED",
        }
    if not shadow:
        return {
            "timeline": ["PLANNER_NOT_REACHED"],
            "deepest_stage": "PLANNER_NOT_REACHED",
        }

    status = str(shadow.get("shadow_status") or shadow.get("planner_status") or "")
    plan = shadow.get("final_plan")
    has_plan = isinstance(plan, dict) and bool(plan.get("steps") or plan.get("final_output"))
    lineage = shadow.get("attempt_lineage") or {}
    attempts = list(lineage.get("attempts") or [])
    has_strong = any(
        (a.get("planner_path") in {"strong", "semantic_strong"}
         or (a.get("planner_model") or "").endswith("32b")
         or (a.get("stage") or "").startswith("failure_escalation")
         or (a.get("stage") or "").startswith("semantic"))
        for a in attempts if isinstance(a, dict)
    )
    pvs = shadow.get("plan_validation_status")
    exs = shadow.get("executor_status")
    rvs = shadow.get("result_validation_status")

    if status in {"running"} and not has_plan:
        timeline.append("PLANNER_NOT_REACHED")
    if shadow.get("cannot_plan") or status == "cannot_plan":
        timeline.append("CANNOT_PLAN")
    if status in {"failed", "planner_failed"} and not has_plan:
        timeline.append("PLANNER_FAILED")
    if has_plan:
        timeline.append("PLAN_CREATED")
    if pvs == "failed" or (isinstance(pvs, str) and pvs not in {"ok", "None", ""} and pvs != "ok"):
        if pvs and pvs != "ok":
            timeline.append("VALIDATION_FAILED")
    if exs is False:
        timeline.append("EXECUTION_FAILED")
    if rvs is False:
        timeline.append("RESULT_VALIDATION_FAILED")
    if shadow.get("failure_32b_invoked"):
        timeline.append("FAILURE_ESCALATION")
    if has_strong:
        timeline.append("STRONG_ATTEMPT_CREATED")
    if shadow.get("semantic_verifier_invoked"):
        timeline.append("VERIFIER_REACHED")
    if shadow.get("semantic_verifier_verdict"):
        timeline.append("VERIFIER_COMPLETED")
    if shadow.get("semantic_32b_invoked"):
        timeline.append("SEMANTIC_ESCALATION")
    if shadow.get("final_attempt_id") or lineage.get("final_attempt_id"):
        timeline.append("FINALIZED")
    if shadow.get("error_family") == "shadow_timeout" or status == "shadow_timeout":
        timeline.append("OPERATIONAL_TIMEOUT")
    if not timeline:
        timeline.append("OTHER")

    deepest = max(timeline, key=lambda s: STAGE_RANK.get(s, 0))
    return {"timeline": timeline, "deepest_stage": deepest}


def classify_timeout(shadow: dict[str, Any], caller_timeout: bool) -> dict[str, Any]:
    status = str(shadow.get("shadow_status") or "")
    err = str(shadow.get("error_family") or "")
    latency = shadow.get("latency_total_s")
    mark = status == "shadow_timeout" or err == "shadow_timeout"
    completed = bool(shadow.get("shadow_completed") or shadow.get("final_attempt_id")
                     or shadow.get("shadow_success"))
    return {
        "caller_timeout": caller_timeout,
        "shadow_timeout_mark": mark,
        "completed_over_threshold": bool(mark and completed),
        "incomplete_no_final_attempt": bool(
            mark and not shadow.get("final_attempt_id")
        ),
        "backend_or_model_timeout_hint": bool(
            "timeout" in str(shadow.get("error_message") or "").lower()
            and err not in {"shadow_timeout", ""}
        ),
        "timeout_type": (
            "caller_timeout" if caller_timeout
            else (
                "shadow_timeout_mark_completed_over_threshold"
                if mark and completed
                else (
                    "shadow_timeout_mark_incomplete"
                    if mark
                    else "none"
                )
            )
        ),
        "latency_total_s": latency,
    }


def classify_failure_32b(shadow: dict[str, Any]) -> dict[str, Any]:
    invoked = bool(shadow.get("failure_32b_invoked"))
    lineage = shadow.get("attempt_lineage") or {}
    attempts = [a for a in (lineage.get("attempts") or []) if isinstance(a, dict)]
    strong = [
        a for a in attempts
        if (a.get("planner_path") in {"strong", "semantic_strong"}
            or str(a.get("escalation_trigger") or "") == "failure_escalation"
            or (a.get("stage") or "").startswith("failure_escalation"))
    ]
    final_path = str(shadow.get("final_path") or "")
    return {
        "failure_escalation_invoked": invoked,
        "trigger_stage": shadow.get("escalation_source"),
        "trigger_reason": final_path if invoked else None,
        "started_32b": invoked,
        "completed_32b": bool(
            invoked and (
                "success" in final_path
                or "cannot_plan" in final_path
                or "failed" in final_path
                or strong
            )
        ),
        "cannot_plan_32b": bool(invoked and (
            shadow.get("cannot_plan") or "cannot_plan" in final_path
        )),
        "backend_timeout_32b": bool(
            invoked and (
                shadow.get("error_family") == "shadow_timeout"
                or shadow.get("shadow_status") == "shadow_timeout"
            )
            and not shadow.get("final_attempt_id")
        ),
        "strong_attempt_created": bool(strong),
        "verifier_eventually_reached": bool(shadow.get("semantic_verifier_invoked")),
        "final_path": final_path or None,
    }


def _attribution_for_request(
    *,
    rid: str,
    rec: dict[str, Any] | None,
    captures: list[dict[str, Any]],
) -> dict[str, Any]:
    if rec is None:
        return {
            "ok": False,
            "attribution_valid": False,
            "reason": "no_identity_bound_telemetry",
            "violations": [{"code": "missing_telemetry", "request_id": rid}],
        }
    if rec.get("request_id") != rid:
        return {
            "ok": False,
            "attribution_valid": False,
            "reason": "telemetry_request_id_mismatch",
            "violations": [{
                "code": "telemetry_request_id_mismatch",
                "expected": rid,
                "got": rec.get("request_id"),
            }],
        }
    shadow = rec.get("shadow") or {}
    lineage = shadow.get("attempt_lineage") or {}
    lin_rep = lineage_integrity_report(
        request_id=rid,
        lineage=lineage,
        verified_attempt_id=shadow.get("verified_attempt_id"),
        final_attempt_id=shadow.get("final_attempt_id") or lineage.get("final_attempt_id"),
    )
    violations = list(lin_rep.get("violations") or [])
    if rec.get("provenance_integrity_failure"):
        violations.append({
            "code": "telemetry_provenance_integrity_failure",
            "reason": rec.get("provenance_integrity_reason"),
        })
    att_by_id = {
        a.get("attempt_id"): a
        for a in (lineage.get("attempts") or [])
        if isinstance(a, dict) and a.get("attempt_id")
    }
    for cap in captures:
        if cap.get("request_id") and cap.get("request_id") != rid:
            violations.append({
                "code": "capture_bound_to_foreign_request",
                "capture_request_id": cap.get("request_id"),
                "expected": rid,
                "attempt_id": cap.get("attempt_id"),
            })
            continue
        aid = cap.get("attempt_id")
        att = att_by_id.get(aid) if aid else None
        cap_rep = capture_integrity_report(
            capture=cap,
            request_id=rid,
            attempt_id=aid,
            plan_fingerprint=(att or {}).get("plan_fingerprint"),
            result_fingerprint=(att or {}).get("result_fingerprint"),
        )
        violations.extend(cap_rep.get("violations") or [])
    ok = not violations
    return {
        "ok": ok,
        "attribution_valid": ok,
        "reason": None if ok else "integrity_violations",
        "violations": violations,
        "lineage_ok": lin_rep.get("ok"),
    }


def _evidence_summary(att: dict[str, Any], caps: list[dict[str, Any]]) -> Any:
    for c in reversed(caps):
        ev = c.get("materialization_evidence") or c.get("evidence")
        if isinstance(ev, dict):
            return {
                "identical_evidence_signature_column_sets": ev.get(
                    "identical_evidence_signature_column_sets"
                ),
                "equivalent_groups": [
                    g.get("final_columns")
                    for g in (ev.get("equivalent_evidence_signature_groups") or [])
                    if isinstance(g, dict)
                ],
                "final_schema": ev.get("final_schema"),
            }
        payload = c.get("payload") or c.get("verifier_payload") or {}
        if isinstance(payload, dict):
            me = payload.get("materialization_evidence") or {}
            if me:
                return {
                    "identical_evidence_signature_column_sets": me.get(
                        "identical_evidence_signature_column_sets"
                    ),
                    "equivalent_groups": [
                        g.get("final_columns")
                        for g in (me.get("equivalent_evidence_signature_groups") or [])
                        if isinstance(g, dict)
                    ],
                    "final_schema": me.get("final_schema"),
                }
    return None


def _attempts_from_shadow(
    *,
    rid: str,
    cid: str,
    family: str,
    shadow: dict[str, Any],
    captures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    lineage = shadow.get("attempt_lineage") or {}
    attempts = list(lineage.get("attempts") or [])
    final_id = shadow.get("final_attempt_id") or lineage.get("final_attempt_id")
    verified_id = shadow.get("verified_attempt_id")
    cap_by_attempt: dict[str, list[dict[str, Any]]] = {}
    for cap in captures:
        if cap.get("request_id") not in {None, rid}:
            continue
        aid = cap.get("attempt_id")
        if aid:
            cap_by_attempt.setdefault(str(aid), []).append(cap)

    rows: list[dict[str, Any]] = []
    for att in attempts:
        aid = att.get("attempt_id")
        caps = cap_by_attempt.get(str(aid), []) if aid else []
        primary = caps[-1] if caps else None
        child_ids = [
            a.get("attempt_id")
            for a in attempts
            if a.get("parent_attempt_id") == aid
        ]
        rows.append({
            "request_id": rid,
            "case_id": cid,
            "family": family,
            "phase": "39S",
            "attempt_id": aid,
            "parent_attempt_id": att.get("parent_attempt_id"),
            "child_attempt_ids": child_ids,
            "attempt_type": att.get("stage") or att.get("attempt_stage"),
            "planner_model": att.get("planner_model"),
            "planner_path": att.get("planner_path"),
            "plan_fingerprint": att.get("plan_fingerprint"),
            "result_fingerprint": att.get("result_fingerprint"),
            "evidence_signature_summary": _evidence_summary(att, caps),
            "escalation_trigger": att.get("escalation_trigger"),
            "attempt_disposition": att.get("disposition") or att.get("attempt_disposition"),
            "became_final": (
                True if final_id and aid == final_id else att.get("became_final")
            ),
            "is_verified_attempt": bool(verified_id and aid == verified_id),
            "is_final_attempt": bool(final_id and aid == final_id),
            "verifier_invocation_ids": [c.get("verifier_invocation_id") for c in caps],
            "verifier_invocations": [{
                "verifier_invocation_id": c.get("verifier_invocation_id"),
                "verdict": c.get("parsed_verdict"),
                "reason_code": c.get("parsed_reason_code"),
                "plan_fingerprint": c.get("plan_fingerprint"),
                "result_fingerprint": c.get("result_fingerprint"),
                "exact_payload_hash": c.get("exact_payload_hash"),
                "attempt_id": c.get("attempt_id"),
                "request_id": c.get("request_id"),
            } for c in caps],
            "primary_verifier_invocation_id": (primary or {}).get("verifier_invocation_id"),
            "primary_verifier_verdict": (primary or {}).get("parsed_verdict"),
            "primary_verifier_reason": (primary or {}).get("parsed_reason_code"),
            "attempt_manual_correct": None,
            "verdict_correctness": None,
            "claim_quality": None,
            "official_metric_eligible": False,
            "attribution_integrity": None,
            "notes_ko": None,
            "lineage_incomplete": False,
        })
    return rows


def _cross_request_flags(request_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    ids = [r["request_id"] for r in request_rows]
    for r in request_rows:
        rid = r["request_id"]
        rec = r.get("raw_shadow_record") or {}
        if rec and rec.get("request_id") and rec.get("request_id") != rid:
            flags.append({
                "code": "telemetry_foreign_id",
                "request_id": rid,
                "got": rec.get("request_id"),
            })
        prompt = r.get("prompt")
        rec_prompt = rec.get("prompt")
        if rec_prompt and prompt and rec_prompt != prompt:
            flags.append({
                "code": "prompt_mismatch",
                "request_id": rid,
                "expected_prompt_prefix": prompt[:80],
                "got_prompt_prefix": str(rec_prompt)[:80],
            })
        vid = r.get("verified_attempt_id") or ""
        fid = r.get("final_attempt_id") or ""
        for other in ids:
            if other == rid:
                continue
            if vid and str(vid).startswith(other):
                flags.append({
                    "code": "verified_attempt_foreign_prefix",
                    "request_id": rid,
                    "verified_attempt_id": vid,
                    "other_request_id": other,
                })
            if fid and str(fid).startswith(other):
                flags.append({
                    "code": "final_attempt_foreign_prefix",
                    "request_id": rid,
                    "final_attempt_id": fid,
                    "other_request_id": other,
                })
    return flags


def write_pilot_request_set(cases: list[dict[str, Any]]) -> Path:
    request_set = [{
        "case_id": c["case_id"],
        "family": c["family"],
        "request_id": c["request_id"],
        "structural_shape": c.get("structural_shape"),
        "prompt": c["prompt"],
        "n_files": len(c["files"]),
        "file_names": list(c["files"]),
        "review_focus": c.get("review_focus"),
        "manual_correctness_criteria": c.get("manual_correctness_criteria"),
        "note": c.get("note"),
    } for c in cases]
    path = OUT / "pilot_request_set.json"
    path.write_text(
        json.dumps({
            "phase": "39S",
            "n": len(request_set),
            "frozen": True,
            "completion_rule": COMPLETION_RULE,
            "family_counts": dict(Counter(c["family"] for c in cases)),
            "cases": request_set,
            "note": (
                "Targeted C/D observation. New IDs/data. No production expected "
                "verdict. Does not copy Phase 39R labels or denominators."
            ),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _apply_shadow_fields(row: dict[str, Any], rec: dict[str, Any], attr: dict[str, Any],
                         caps_for_req: list[dict[str, Any]]) -> None:
    shadow = rec.get("shadow") or {}
    stages = classify_stages(shadow, rec)
    timeout = classify_timeout(shadow, bool(row.get("caller_timeout")))
    fe = classify_failure_32b(shadow)
    lineage = shadow.get("attempt_lineage") or {}
    row["raw_shadow_record"] = rec
    row["shadow_recorded"] = True
    row["shadow"] = shadow
    row["shadow_status"] = shadow.get("shadow_status") or shadow.get("status")
    row["shadow_success"] = shadow.get("shadow_success")
    row["cannot_plan"] = shadow.get("cannot_plan")
    row["fast_planner_status"] = shadow.get("planner_status") or shadow.get("shadow_status")
    row["plan_validation_status"] = shadow.get("plan_validation_status")
    row["executor_status"] = shadow.get("executor_status")
    row["result_validation_status"] = shadow.get("result_validation_status")
    row["verifier_invoked"] = shadow.get("semantic_verifier_invoked")
    row["verifier_verdict"] = shadow.get("semantic_verifier_verdict")
    row["verifier_reason"] = shadow.get("semantic_verifier_reason")
    row["failure_32b"] = shadow.get("failure_32b_invoked")
    row["semantic_32b"] = shadow.get("semantic_32b_invoked")
    row["final_path"] = shadow.get("final_path")
    row["escalation_source"] = shadow.get("escalation_source")
    row["shadow_latency_s"] = shadow.get("latency_total_s")
    row["latency_by_stage_s"] = shadow.get("latency_by_stage_s")
    row["final_plan"] = shadow.get("final_plan")
    row["final_plan_ops"] = _ops_summary(shadow.get("final_plan"))
    row["result_fingerprint"] = shadow.get("result_fingerprint")
    row["attempt_lineage"] = lineage
    row["verified_attempt_id"] = shadow.get("verified_attempt_id")
    row["final_attempt_id"] = (
        shadow.get("final_attempt_id") or lineage.get("final_attempt_id")
    )
    row["verified_plan_fingerprint"] = shadow.get("verified_plan_fingerprint")
    row["final_plan_fingerprint"] = shadow.get("final_plan_fingerprint")
    row["shadow_error_family"] = shadow.get("error_family")
    row["shadow_error_message"] = shadow.get("error_message")
    row["model_calls"] = shadow.get("model_calls")
    row["n_verifier_captures"] = len(caps_for_req)
    row["capture_invocation_ids"] = [c.get("verifier_invocation_id") for c in caps_for_req]
    row["attribution_integrity"] = attr["ok"]
    row["attribution_report"] = attr
    row["legacy_telemetry"] = rec.get("legacy") or row.get("legacy_telemetry")
    row["stage_timeline"] = stages["timeline"]
    row["deepest_stage"] = stages["deepest_stage"]
    row["timeout_detail"] = timeout
    row["timeout_type"] = timeout["timeout_type"]
    row["failure_escalation_detail"] = fe
    row["verifier_exposure"] = bool(
        attr["ok"]
        and shadow.get("semantic_verifier_invoked")
        and shadow.get("semantic_verifier_verdict")
        and (shadow.get("verified_attempt_id") or caps_for_req)
    )


def run_pilot() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    TEL.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    CAPTURE.mkdir(parents=True, exist_ok=True)

    import inspect
    from core.integrate.semantic_verifier import run_semantic_verification

    mode = inspect.signature(run_semantic_verification).parameters[
        "materialization_mode"
    ].default
    if mode != MATERIALIZATION:
        raise RuntimeError(
            f"Refuse observation: materialization={mode!r}, need {MATERIALIZATION}"
        )

    tel_abs = str(TEL.resolve())
    cap_abs = str(CAPTURE.resolve())
    os.environ["MULTI_SHADOW_ENABLED"] = "true"
    os.environ["MULTI_SHADOW_SAMPLE_RATE"] = "1.0"
    os.environ["MULTI_SHADOW_INLINE_FOR_TESTS"] = "false"
    os.environ["MULTI_SHADOW_TELEMETRY_DIR"] = tel_abs
    os.environ["MULTI_SHADOW_STORE_PROMPT"] = "true"
    os.environ["MULTI_SHADOW_MAX_CONCURRENCY"] = "1"
    os.environ["MULTI_SHADOW_QUEUE_SIZE"] = "8"
    os.environ["MULTI_SHADOW_TIMEOUT_SEC"] = "600"
    os.environ["MULTI_VERIFIER_CAPTURE_DIR"] = cap_abs
    os.environ["MULTI_VERIFIER_CAPTURE_ENABLED"] = "true"

    reset_shadow_worker_for_tests()
    cfg = reload_config_for_tests()
    print(
        "Shadow config:",
        f"enabled={cfg.enabled}",
        f"telemetry_dir={cfg.telemetry_dir}",
        f"capture_dir={cap_abs}",
        f"materialization={MATERIALIZATION}",
        f"completion_rule={COMPLETION_RULE['name']}",
        f"timeout_sec={cfg.timeout_sec}",
        flush=True,
    )
    if not cfg.enabled:
        raise RuntimeError("Shadow failed to enable for session")
    if float(cfg.timeout_sec) != 600.0:
        raise RuntimeError(f"Refuse observation: timeout inflated to {cfg.timeout_sec}")

    cases = _cases()
    write_pilot_request_set(cases)

    request_rows: list[dict[str, Any]] = []
    attempt_rows: list[dict[str, Any]] = []
    stop_reason: str | None = None

    for i, case in enumerate(cases, 1):
        cid = case["case_id"]
        rid = case["request_id"]
        print(f"\n=== [{i}/{len(cases)}] {cid} {rid} {case['family']} ===", flush=True)
        case_dir = DATA / cid
        case_dir.mkdir(parents=True, exist_ok=True)
        named: list[tuple[str, pd.DataFrame]] = []
        for name, df in case["files"].items():
            df.to_excel(case_dir / name, index=False)
            named.append((name, df.copy()))

        os.environ["MULTI_VERIFIER_CAPTURE_CASE_ID"] = cid
        os.environ["MULTI_VERIFIER_CAPTURE_REQUEST_ID"] = rid

        t0 = time.time()
        legacy_err = None
        outcome = None
        try:
            outcome = route_multi_prompt(
                case["prompt"],
                named_frames=named,
                base_url=BASE_URL,
                model=MODEL,
                profile_name=None,
                context_label=None,
                filter_df=None,
                request_id=rid,
                case_id=cid,
            )
        except Exception as exc:  # noqa: BLE001
            legacy_err = f"{type(exc).__name__}: {exc}"
            print("LEGACY EXCEPTION", legacy_err, flush=True)

        legacy_latency = round(time.time() - t0, 3)
        legacy_reply = getattr(outcome, "reply", None) if outcome else None
        legacy_op = getattr(outcome, "operation_name", None) if outcome else None
        legacy_df = getattr(outcome, "dataframe", None) if outcome else None

        shadow_rec, caller_timeout = await_identity_record(rid, timeout_s=CALLER_WAIT_SEC)
        if shadow_rec is None:
            print(f"  caller_timeout identity miss {rid}", flush=True)
        else:
            print(
                f"  identity hit {rid} timeout={caller_timeout} "
                f"recorded={shadow_rec.get('recorded_at_utc')}",
                flush=True,
            )

        caps_for_req = [c for c in _load_all_captures() if c.get("request_id") == rid]
        shadow = (shadow_rec or {}).get("shadow") or {}
        legacy_tel = (shadow_rec or {}).get("legacy") or {}
        attr = _attribution_for_request(rid=rid, rec=shadow_rec, captures=caps_for_req)

        if not attr["ok"] and shadow_rec is not None:
            stop_reason = f"STOP isolation: attribution failed on {cid} {rid}"
            print(stop_reason, attr.get("violations"), flush=True)

        att_rows = _attempts_from_shadow(
            rid=rid,
            cid=cid,
            family=case["family"],
            shadow=shadow,
            captures=caps_for_req,
        )
        for ar in att_rows:
            ar["attribution_integrity"] = attr["ok"]
            attempt_rows.append(ar)

        stages = classify_stages(shadow, shadow_rec)
        timeout = classify_timeout(shadow, caller_timeout)
        fe = classify_failure_32b(shadow)
        row = {
            "request_id": rid,
            "case_id": cid,
            "phase": "39S",
            "family": case["family"],
            "structural_shape": case.get("structural_shape"),
            "prompt": case["prompt"],
            "files": list(case["files"]),
            "review_focus": case.get("review_focus"),
            "manual_correctness_criteria": case.get("manual_correctness_criteria"),
            "legacy_status": (
                "exception" if legacy_err else (
                    "success" if legacy_df is not None
                    or (legacy_reply and len(str(legacy_reply)) > 0)
                    else "unknown"
                )
            ),
            "legacy_operation": legacy_op,
            "legacy_reply_preview": (str(legacy_reply or ""))[:400],
            "legacy_latency_s": legacy_latency,
            "legacy_error": legacy_err,
            "legacy_telemetry": legacy_tel,
            "shadow_recorded": shadow_rec is not None,
            "caller_timeout": caller_timeout,
            "late_completion": False,
            "raw_shadow_record": shadow_rec,
            "final_shadow_correct": None,
            "manual_review": None,
            "notes_ko": None,
            "operational_failure": None,
        }
        if shadow_rec is not None:
            _apply_shadow_fields(row, shadow_rec, attr, caps_for_req)
        else:
            row.update({
                "attribution_integrity": False,
                "attribution_report": attr,
                "deepest_stage": "PLANNER_NOT_REACHED",
                "stage_timeline": ["PLANNER_NOT_REACHED"],
                "timeout_type": "caller_timeout" if caller_timeout else "none",
                "timeout_detail": timeout,
                "failure_escalation_detail": fe,
                "verifier_exposure": False,
                "n_attempts": 0,
            })
        row["n_attempts"] = len(att_rows)
        request_rows.append(row)
        _write_logs(request_rows, attempt_rows)
        print(
            "legacy", row["legacy_status"],
            "shadow", row["shadow_recorded"], row.get("shadow_status"),
            "attr", row.get("attribution_integrity"),
            "deepest", row.get("deepest_stage"),
            "verifier", row.get("verifier_exposure"),
            "fail32b", row.get("failure_32b"),
            "final_attempt", (row.get("final_attempt_id") or "")[:40],
            flush=True,
        )
        xflags = _cross_request_flags(request_rows)
        if xflags:
            stop_reason = f"STOP isolation: cross-request contamination {xflags[0]}"
            print(stop_reason, flush=True)
        if stop_reason:
            break

    print("\n=== end-of-set drain ===", flush=True)
    drain = drain_inflight(timeout_s=END_DRAIN_SEC)
    print("drain", drain, flush=True)

    all_tel = _load_all_telemetry()
    all_caps = _load_all_captures()
    bound = bind_records_by_request_id(all_tel)
    for row in request_rows:
        rid = row["request_id"]
        rec = bound.get(rid)
        had = row.get("raw_shadow_record") is not None
        if rec is not None:
            row["late_completion"] = bool(row.get("caller_timeout")) and (
                not had or rec != row.get("raw_shadow_record")
            )
            if row.get("caller_timeout") and rec is not None:
                row["late_completion"] = True
            caps_for_req = [c for c in all_caps if c.get("request_id") == rid]
            attr = _attribution_for_request(rid=rid, rec=rec, captures=caps_for_req)
            _apply_shadow_fields(row, rec, attr, caps_for_req)
            shadow = rec.get("shadow") or {}
            new_atts = _attempts_from_shadow(
                rid=rid,
                cid=row["case_id"],
                family=row["family"],
                shadow=shadow,
                captures=caps_for_req,
            )
            for ar in new_atts:
                ar["attribution_integrity"] = attr["ok"]
            attempt_rows[:] = [a for a in attempt_rows if a.get("request_id") != rid]
            attempt_rows.extend(new_atts)
            row["n_attempts"] = len(new_atts)

    xflags = _cross_request_flags(request_rows)
    if xflags and not stop_reason:
        stop_reason = (
            f"STOP isolation: cross-request contamination after drain {xflags[0]}"
        )

    cap_index = []
    for c in all_caps:
        cap_index.append({
            "verifier_invocation_id": c.get("verifier_invocation_id"),
            "request_id": c.get("request_id"),
            "case_id": c.get("case_id"),
            "attempt_id": c.get("attempt_id"),
            "plan_fingerprint": c.get("plan_fingerprint"),
            "result_fingerprint": c.get("result_fingerprint"),
            "parsed_verdict": c.get("parsed_verdict"),
            "parsed_reason_code": c.get("parsed_reason_code"),
            "exact_payload_hash": c.get("exact_payload_hash"),
            "became_final": c.get("became_final"),
            "final_attempt_id": c.get("final_attempt_id"),
            "parent_attempt_id": c.get("parent_attempt_id"),
            "escalation_trigger": c.get("escalation_trigger"),
        })
    (OUT / "verifier_capture_index.json").write_text(
        json.dumps({"phase": "39S", "n": len(cap_index), "rows": cap_index}, indent=2),
        encoding="utf-8",
    )

    attr_report = {
        "phase": "39S",
        "completion_rule": COMPLETION_RULE,
        "drain": drain,
        "n_requests": len(request_rows),
        "telemetry_covered": sum(1 for r in request_rows if r.get("shadow_recorded")),
        "attribution_valid": sum(1 for r in request_rows if r.get("attribution_integrity")),
        "caller_timeouts": sum(1 for r in request_rows if r.get("caller_timeout")),
        "late_completions": sum(1 for r in request_rows if r.get("late_completion")),
        "cross_request_flags": xflags,
        "cross_request_contamination_count": len(xflags),
        "stop_reason": stop_reason,
        "per_request": [
            {
                "request_id": r["request_id"],
                "case_id": r["case_id"],
                "family": r.get("family"),
                "attribution_integrity": r.get("attribution_integrity"),
                "violations": (r.get("attribution_report") or {}).get("violations"),
                "caller_timeout": r.get("caller_timeout"),
                "late_completion": r.get("late_completion"),
                "deepest_stage": r.get("deepest_stage"),
                "verifier_exposure": r.get("verifier_exposure"),
                "verified_attempt_id": r.get("verified_attempt_id"),
                "final_attempt_id": r.get("final_attempt_id"),
            }
            for r in request_rows
        ],
    }
    (OUT / "attribution_integrity_report.json").write_text(
        json.dumps(attr_report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    _write_logs(request_rows, attempt_rows)
    (OUT / "observation_bundle.json").write_text(
        json.dumps({
            "phase": "39S",
            "n_requests": len(request_rows),
            "n_attempts": len(attempt_rows),
            "stop_reason": stop_reason,
            "drain": drain,
            "requests": request_rows,
            "attempts": attempt_rows,
        }, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    os.environ["MULTI_SHADOW_ENABLED"] = "false"
    os.environ.pop("MULTI_VERIFIER_CAPTURE_DIR", None)
    os.environ["MULTI_VERIFIER_CAPTURE_ENABLED"] = "false"
    cfg_off = reload_config_for_tests()
    proof = {
        "phase": "39S",
        "MULTI_SHADOW_ENABLED_env": os.environ.get("MULTI_SHADOW_ENABLED"),
        "config_enabled": bool(cfg_off.enabled),
        "confirmed_off": not bool(cfg_off.enabled),
        "MULTI_VERIFIER_CAPTURE_ENABLED_env": os.environ.get(
            "MULTI_VERIFIER_CAPTURE_ENABLED"
        ),
        "confirmed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "migration": "NOT_APPROVED",
        "stop_reason": stop_reason,
    }
    (OUT / "shadow_off_proof.json").write_text(
        json.dumps(proof, indent=2), encoding="utf-8"
    )
    print("\nShadow returned to OFF", proof, flush=True)
    return {
        "request_rows": request_rows,
        "attempt_rows": attempt_rows,
        "stop_reason": stop_reason,
        "attr_report": attr_report,
    }


def _write_logs(
    request_rows: list[dict[str, Any]], attempt_rows: list[dict[str, Any]]
) -> None:
    (OUT / "request_observation_log.json").write_text(
        json.dumps(
            {"phase": "39S", "n": len(request_rows), "rows": request_rows},
            ensure_ascii=False, indent=2, default=str,
        ),
        encoding="utf-8",
    )
    (OUT / "attempt_observation_log.json").write_text(
        json.dumps(
            {"phase": "39S", "n": len(attempt_rows), "rows": attempt_rows},
            ensure_ascii=False, indent=2, default=str,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    run_pilot()
