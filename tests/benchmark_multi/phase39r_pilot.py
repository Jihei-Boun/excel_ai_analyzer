"""Phase 39R — Clean attempt-aware Shadow revalidation after 39Q isolation.

Observation only. Identity-based collection (never new_recs[-1]).
Does not reuse Phase 39P labels, telemetry, or denominators.
"""

from __future__ import annotations

import json
import os
import statistics
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
OUT = ROOT / "benchmark_results/multi/phase39r"
TEL = OUT / "telemetry"
DATA = OUT / "datasets"
CAPTURE = OUT / "verifier_captures"

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = "qwen2.5:7b"
MATERIALIZATION = "final_schema_expr_partition"

# Existing system timeout (not inflated). Caller wait uses the same 1800s
# budget as prior observation harnesses; association is by request_id.
CALLER_WAIT_SEC = 1800.0
END_DRAIN_SEC = 1800.0


def _cases() -> list[dict[str, Any]]:
    """New 15-request observation set. Conceptual families only — new IDs/data."""
    cases: list[dict[str, Any]] = []

    # A. Ordinary valid multi-file (3)
    cases.append({
        "case_id": "P39R-01",
        "family": "ordinary_multi",
        "category": "A_ordinary",
        "prompt": (
            "Join tickets to agents on agent_id and list ticket_id, "
            "agent_name, and minutes."
        ),
        "files": {
            "tickets.xlsx": pd.DataFrame({
                "ticket_id": [101, 102, 103],
                "agent_id": ["G1", "G2", "G1"],
                "minutes": [20, 35, 15],
            }),
            "agents.xlsx": pd.DataFrame({
                "agent_id": ["G1", "G2"],
                "agent_name": ["Mina", "Leo"],
            }),
        },
        "review_focus": "Ordinary join; request isolation and final-attempt binding.",
        "note": "New ordinary join control (not 39P data).",
    })
    cases.append({
        "case_id": "P39R-02",
        "family": "ordinary_multi",
        "category": "A_ordinary",
        "prompt": (
            "Join parcels to lockers on locker_id, then show total weight by locker_name."
        ),
        "files": {
            "parcels.xlsx": pd.DataFrame(
                {"locker_id": ["K1", "K1", "K2"], "weight": [2.0, 3.5, 1.5]}
            ),
            "lockers.xlsx": pd.DataFrame(
                {"locker_id": ["K1", "K2"], "locker_name": ["North", "South"]}
            ),
        },
        "review_focus": "Ordinary aggregate-after-join.",
        "note": "New ordinary aggregate-after-join.",
    })
    cases.append({
        "case_id": "P39R-03",
        "family": "ordinary_multi",
        "category": "A_ordinary",
        "prompt": (
            "Stack the two incident logs into one table with incident_id, floor, and ticks."
        ),
        "files": {
            "incidents_am.xlsx": pd.DataFrame(
                {"incident_id": ["I1", "I2"], "floor": ["F1", "F1"], "ticks": [4, 6]}
            ),
            "incidents_pm.xlsx": pd.DataFrame(
                {"incident_id": ["I3", "I4"], "floor": ["F2", "F2"], "ticks": [2, 7]}
            ),
        },
        "review_focus": "Ordinary union/stack; columns must match prompt.",
        "note": "New ordinary union/stack.",
    })

    # B. Valid rename+join / genuine dual (4)
    cases.append({
        "case_id": "P39R-04",
        "family": "valid_rename_join",
        "category": "B_valid_rename_join",
        "prompt": (
            "Show pond_north_liters and pond_south_liters for each tank_id after "
            "combining the two pond inventory files."
        ),
        "files": {
            "pond_north.xlsx": pd.DataFrame({"tank_id": ["T1", "T2"], "liters": [40, 22]}),
            "pond_south.xlsx": pd.DataFrame({"tank_id": ["T1", "T2"], "liters": [35, 28]}),
        },
        "review_focus": "Genuine dual rename+join; valid dual evidence must not be rejected.",
        "note": "New dual liters rename+join.",
    })
    cases.append({
        "case_id": "P39R-05",
        "family": "valid_rename_join",
        "category": "B_valid_rename_join",
        "prompt": (
            "Show gate_in_pallets and gate_out_pallets for each crate_id after "
            "combining the two gate inventory files."
        ),
        "files": {
            "gate_in.xlsx": pd.DataFrame({"crate_id": ["CR1", "CR2"], "pallets": [9, 4]}),
            "gate_out.xlsx": pd.DataFrame({"crate_id": ["CR1", "CR2"], "pallets": [7, 5]}),
        },
        "review_focus": "Genuine dual rename+join (historical FF-family shape, new data).",
        "note": "New dual pallets rename+join.",
    })
    cases.append({
        "case_id": "P39R-06",
        "family": "valid_rename_join",
        "category": "B_valid_rename_join",
        "prompt": (
            "Show roof_a_lux and roof_b_lux for each sensor_id after combining "
            "the two roof light files."
        ),
        "files": {
            "roof_a.xlsx": pd.DataFrame({"sensor_id": ["U1", "U2"], "lux": [110.0, 95.0]}),
            "roof_b.xlsx": pd.DataFrame({"sensor_id": ["U1", "U2"], "lux": [102.0, 99.0]}),
        },
        "review_focus": "Genuine dual rename+join energy-like pair.",
        "note": "New dual lux rename+join.",
    })
    cases.append({
        "case_id": "P39R-07",
        "family": "valid_rename_join",
        "category": "B_valid_rename_join",
        "prompt": (
            "Show cafe_p_drinks and cafe_q_drinks for each item_id after combining "
            "the two cafe sales files."
        ),
        "files": {
            "cafe_p.xlsx": pd.DataFrame({"item_id": ["D1", "D2"], "drinks": [30, 18]}),
            "cafe_q.xlsx": pd.DataFrame({"item_id": ["D1", "D2"], "drinks": [22, 25]}),
        },
        "review_focus": "Additional genuine dual rename+join.",
        "note": "New dual drinks rename+join.",
    })

    # C. Fake-dual / collapse pressure (3) — P39G-11-like structure
    cases.append({
        "case_id": "P39R-08",
        "family": "fake_dual",
        "category": "C_fake_dual",
        "prompt": (
            "Compare batch B1 versus batch B2 yield totals by station_id and "
            "keep both batch totals visible."
        ),
        "files": {
            "yield_all.xlsx": pd.DataFrame({
                "station_id": ["ST1", "ST1", "ST2", "ST2"],
                "batch": ["B1", "B2", "B1", "B2"],
                "yield": [10, 12, 8, 9],
            }),
            "stations.xlsx": pd.DataFrame(
                {"station_id": ["ST1", "ST2"], "hall": ["H1", "H2"]}
            ),
        },
        "family_ref": "P39G-11",
        "review_focus": (
            "Fake-dual pressure: aliases of the same population are not two sides. "
            "Parent collapse may be CORRECT_REJECTION; child recovery is separate."
        ),
        "escalation_interest": True,
        "note": "P39G-11-like fake-dual with lookup file for Shadow eligibility.",
    })
    cases.append({
        "case_id": "P39R-09",
        "family": "fake_dual",
        "category": "C_fake_dual",
        "prompt": (
            "Compare wave morning versus wave evening output by cell_id and keep "
            "both wave totals visible."
        ),
        "files": {
            "cell_output.xlsx": pd.DataFrame({
                "cell_id": ["CE1", "CE1", "CE2", "CE2"],
                "wave": ["morning", "evening", "morning", "evening"],
                "output": [40, 42, 35, 38],
            }),
            "cells.xlsx": pd.DataFrame(
                {"cell_id": ["CE1", "CE2"], "block": ["BL1", "BL2"]}
            ),
        },
        "review_focus": "Second fake-dual collapse pressure.",
        "escalation_interest": True,
        "note": "Fake-dual wave comparison.",
    })
    cases.append({
        "case_id": "P39R-10",
        "family": "fake_dual",
        "category": "C_fake_dual",
        "prompt": (
            "Compare cohort Alpha versus cohort Beta demand by sku_code and keep "
            "both cohort totals visible."
        ),
        "files": {
            "demand_all.xlsx": pd.DataFrame({
                "sku_code": ["Q1", "Q1", "Q2", "Q2"],
                "cohort": ["Alpha", "Beta", "Alpha", "Beta"],
                "demand": [7, 9, 5, 6],
            }),
            "skus.xlsx": pd.DataFrame(
                {"sku_code": ["Q1", "Q2"], "family": ["F1", "F2"]}
            ),
        },
        "review_focus": "Third fake-dual collapse pressure.",
        "escalation_interest": True,
        "note": "Fake-dual cohort comparison.",
    })

    # D. Same-origin independently partitioned valid comparison (2)
    cases.append({
        "case_id": "P39R-11",
        "family": "same_origin_partitioned",
        "category": "D_same_origin_partitioned",
        "prompt": (
            "Using the probe readings, compare day D1 versus day D2 totals by "
            "probe_id and keep both day totals visible."
        ),
        "files": {
            "probe_readings.xlsx": pd.DataFrame({
                "probe_id": ["PR1", "PR1", "PR2", "PR2"],
                "day": ["D1", "D2", "D1", "D2"],
                "kwh": [3, 4, 5, 6],
            }),
            "probes.xlsx": pd.DataFrame(
                {"probe_id": ["PR1", "PR2"], "wing": ["W1", "W2"]}
            ),
        },
        "review_focus": (
            "Same origin does not imply same evidence. Independent day partitions "
            "may be a valid PASS (Phase 39H)."
        ),
        "note": "Same-origin partitioned valid comparison.",
    })
    cases.append({
        "case_id": "P39R-12",
        "family": "same_origin_partitioned",
        "category": "D_same_origin_partitioned",
        "prompt": (
            "Using kiln readings, compare cycle C1 versus cycle C2 totals by "
            "kiln_id and keep both cycle totals visible."
        ),
        "files": {
            "kiln_readings.xlsx": pd.DataFrame({
                "kiln_id": ["Y1", "Y1", "Y2", "Y2"],
                "cycle": ["C1", "C2", "C1", "C2"],
                "units": [10, 12, 8, 11],
            }),
            "kilns.xlsx": pd.DataFrame(
                {"kiln_id": ["Y1", "Y2"], "bay": ["BA1", "BA2"]}
            ),
        },
        "review_focus": "Second independently partitioned same-origin comparison.",
        "note": "Same-origin partitioned valid comparison.",
    })

    # E. Escalation-prone difficult cases (2)
    cases.append({
        "case_id": "P39R-13",
        "family": "escalation_pressure",
        "category": "E_escalation_pressure",
        "prompt": (
            "Show aisle_left_stock and aisle_right_stock side by side for each "
            "sku_key from the two aisle extracts. Do not collapse into one total."
        ),
        "files": {
            "aisle_left.xlsx": pd.DataFrame(
                {"sku_key": ["SK1", "SK2", "SK3"], "stock": [10, 0, 4]}
            ),
            "aisle_right.xlsx": pd.DataFrame(
                {"sku_key": ["SK1", "SK2", "SK4"], "stock": [8, 5, 2]}
            ),
        },
        "review_focus": "Asymmetric keys; dual-side preservation; recovery value if escalated.",
        "escalation_interest": True,
        "note": "Asymmetric dual-stock pressure.",
    })
    cases.append({
        "case_id": "P39R-14",
        "family": "escalation_pressure",
        "category": "E_escalation_pressure",
        "prompt": (
            "Combine pier_west and pier_east berth tables so each slip_id has "
            "pier_west_berths and pier_east_berths columns visible together."
        ),
        "files": {
            "pier_west.xlsx": pd.DataFrame({"slip_id": ["SL1", "SL2"], "berths": [4, 6]}),
            "pier_east.xlsx": pd.DataFrame({"slip_id": ["SL1", "SL2"], "berths": [3, 5]}),
        },
        "review_focus": "Dual-berth rename+join pressure; escalation observational only.",
        "escalation_interest": True,
        "note": "Dual-berth rename+join pressure.",
    })

    # F. Ambiguous / cannot-plan (1)
    cases.append({
        "case_id": "P39R-15",
        "family": "ambiguous",
        "category": "F_ambiguous",
        "prompt": "Do the right multi-file analysis for these spreadsheets.",
        "files": {
            "gamma.xlsx": pd.DataFrame({"id": [1], "p": [1]}),
            "delta.xlsx": pd.DataFrame({"id": [1], "q": [2]}),
        },
        "review_focus": "Ambiguous prompt; cannot-plan / refusal is acceptable.",
        "note": "Ambiguous; inability OK.",
    })
    return cases


COMPLETION_RULE = {
    "name": "identity_bound_finalization",
    "text": (
        "A case is complete enough for review when a telemetry record exists "
        "whose request_id equals the submitted request_id. Caller wait budget "
        "is 1800s (existing harness budget, not inflated). If the budget expires "
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
    """Wait for telemetry bound to this request_id. Never return a foreign record.

    Returns (record_or_none, caller_timeout).
    """
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


def _attempts_from_shadow(
    *,
    rid: str,
    cid: str,
    family: str,
    category: str,
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
            "category": category,
            "phase": "39R",
            "attempt_id": aid,
            "parent_attempt_id": att.get("parent_attempt_id"),
            "child_attempt_ids": child_ids,
            "attempt_type": att.get("stage") or att.get("attempt_stage"),
            "planner_model": att.get("planner_model"),
            "planner_path": att.get("planner_path"),
            "plan_fingerprint": att.get("plan_fingerprint"),
            "result_fingerprint": att.get("result_fingerprint"),
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
            "classification_32b": None,
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
            flags.append({"code": "telemetry_foreign_id", "request_id": rid, "got": rec.get("request_id")})
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
        "category": c["category"],
        "prompt": c["prompt"],
        "n_files": len(c["files"]),
        "file_names": list(c["files"]),
        "family_ref": c.get("family_ref"),
        "escalation_interest": bool(c.get("escalation_interest")),
        "review_focus": c.get("review_focus"),
        "note": c.get("note"),
    } for c in cases]
    path = OUT / "pilot_request_set.json"
    path.write_text(
        json.dumps({
            "phase": "39R",
            "n": len(request_set),
            "frozen": True,
            "completion_rule": COMPLETION_RULE,
            "family_counts": dict(Counter(c["family"] for c in cases)),
            "category_counts": dict(Counter(c["category"] for c in cases)),
            "cases": request_set,
            "note": (
                "New observation dataset. Does not copy Phase 39P labels, "
                "telemetry, or denominators. No production expected verdict."
            ),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


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
        flush=True,
    )
    if not cfg.enabled:
        raise RuntimeError("Shadow failed to enable for session")

    cases = _cases()
    write_pilot_request_set(cases)

    request_rows: list[dict[str, Any]] = []
    attempt_rows: list[dict[str, Any]] = []
    stop_reason: str | None = None
    submitted_at: dict[str, float] = {}

    for i, case in enumerate(cases, 1):
        cid = case["case_id"]
        rid = f"p39r-req-{i:02d}"
        print(f"\n=== [{i}/{len(cases)}] {cid} {rid} {case['family']} ===", flush=True)
        case_dir = DATA / cid
        case_dir.mkdir(parents=True, exist_ok=True)
        named: list[tuple[str, pd.DataFrame]] = []
        for name, df in case["files"].items():
            df.to_excel(case_dir / name, index=False)
            named.append((name, df.copy()))

        # Freeze identity on caller thread (also passed explicitly into route_multi).
        os.environ["MULTI_VERIFIER_CAPTURE_CASE_ID"] = cid
        os.environ["MULTI_VERIFIER_CAPTURE_REQUEST_ID"] = rid

        t0 = time.time()
        submitted_at[rid] = t0
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
        late_at_wait = False
        if shadow_rec is None:
            print(f"  caller_timeout identity miss {rid}", flush=True)
        else:
            rec_at = shadow_rec.get("recorded_at_utc")
            print(
                f"  identity hit {rid} timeout={caller_timeout} "
                f"recorded={rec_at}",
                flush=True,
            )

        caps_for_req = [
            c for c in _load_all_captures() if c.get("request_id") == rid
        ]
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
            category=case["category"],
            shadow=shadow,
            captures=caps_for_req,
        )
        for ar in att_rows:
            ar["attribution_integrity"] = attr["ok"]
            ar["official_metric_eligible"] = False
            attempt_rows.append(ar)

        lineage = shadow.get("attempt_lineage") or {}
        final_attempt_id = shadow.get("final_attempt_id") or lineage.get("final_attempt_id")
        verified_attempt_id = shadow.get("verified_attempt_id")
        row = {
            "request_id": rid,
            "case_id": cid,
            "phase": "39R",
            "candidate_version": "Phase39Q-isolation+Phase39O-lineage+V2.2",
            "materialization_mode": MATERIALIZATION,
            "family": case["family"],
            "category": case["category"],
            "family_ref": case.get("family_ref"),
            "prompt": case["prompt"],
            "files": list(case["files"]),
            "review_focus": case.get("review_focus"),
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
            "legacy_correct": None,
            "shadow_recorded": shadow_rec is not None,
            "caller_timeout": caller_timeout,
            "late_completion": late_at_wait,
            "shadow_status": shadow.get("shadow_status") or shadow.get("status")
            or (shadow_rec or {}).get("event"),
            "shadow_success": shadow.get("shadow_success"),
            "cannot_plan": shadow.get("cannot_plan"),
            "verifier_invoked": shadow.get("semantic_verifier_invoked"),
            "verifier_verdict": shadow.get("semantic_verifier_verdict"),
            "verifier_reason": shadow.get("semantic_verifier_reason"),
            "failure_32b": shadow.get("failure_32b_invoked"),
            "semantic_32b": shadow.get("semantic_32b_invoked"),
            "final_path": shadow.get("final_path"),
            "escalation_source": shadow.get("escalation_source"),
            "shadow_latency_s": shadow.get("latency_total_s"),
            "latency_by_stage_s": shadow.get("latency_by_stage_s"),
            "final_plan": shadow.get("final_plan"),
            "final_plan_ops": _ops_summary(shadow.get("final_plan")),
            "result_fingerprint": shadow.get("result_fingerprint"),
            "attempt_lineage": lineage,
            "verified_attempt_id": verified_attempt_id,
            "final_attempt_id": final_attempt_id,
            "verified_plan_fingerprint": shadow.get("verified_plan_fingerprint"),
            "final_plan_fingerprint": shadow.get("final_plan_fingerprint"),
            "n_attempts": len(att_rows),
            "n_verifier_captures": len(caps_for_req),
            "capture_invocation_ids": [
                c.get("verifier_invocation_id") for c in caps_for_req
            ],
            "comparison": (shadow_rec or {}).get("comparison"),
            "shadow_error_family": shadow.get("error_family"),
            "shadow_error_message": shadow.get("error_message"),
            "model_calls": shadow.get("model_calls"),
            "raw_shadow_record": shadow_rec,
            "attribution_integrity": attr["ok"],
            "attribution_report": attr,
            "final_shadow_correct": None,
            "manual_review": None,
            "notes_ko": None,
        }
        request_rows.append(row)
        _write_logs(request_rows, attempt_rows)
        print(
            "legacy", row["legacy_status"],
            "shadow", row["shadow_recorded"], row["shadow_status"],
            "attr", row["attribution_integrity"],
            "timeout", row["caller_timeout"],
            "final_attempt", (final_attempt_id or "")[:36],
            "attempts", len(att_rows),
            "caps", len(caps_for_req),
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

    # Re-bind late completions by identity.
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
            if row["caller_timeout"] and rec is not None:
                row["late_completion"] = True
            shadow = rec.get("shadow") or {}
            caps_for_req = [c for c in all_caps if c.get("request_id") == rid]
            attr = _attribution_for_request(rid=rid, rec=rec, captures=caps_for_req)
            row["raw_shadow_record"] = rec
            row["shadow_recorded"] = True
            row["shadow"] = shadow
            row["shadow_status"] = shadow.get("shadow_status") or shadow.get("status")
            row["shadow_success"] = shadow.get("shadow_success")
            row["cannot_plan"] = shadow.get("cannot_plan")
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
            lineage = shadow.get("attempt_lineage") or {}
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
            row["capture_invocation_ids"] = [
                c.get("verifier_invocation_id") for c in caps_for_req
            ]
            row["attribution_integrity"] = attr["ok"]
            row["attribution_report"] = attr
            row["legacy_telemetry"] = rec.get("legacy") or row.get("legacy_telemetry")
            # Rebuild attempts from late-bound record (identity only).
            new_atts = _attempts_from_shadow(
                rid=rid,
                cid=row["case_id"],
                family=row["family"],
                category=row["category"],
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
        stop_reason = f"STOP isolation: cross-request contamination after drain {xflags[0]}"

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
        json.dumps({"phase": "39R", "n": len(cap_index), "rows": cap_index}, indent=2),
        encoding="utf-8",
    )

    attr_report = {
        "phase": "39R",
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
                "attribution_integrity": r.get("attribution_integrity"),
                "violations": (r.get("attribution_report") or {}).get("violations"),
                "caller_timeout": r.get("caller_timeout"),
                "late_completion": r.get("late_completion"),
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
            "phase": "39R",
            "n_requests": len(request_rows),
            "n_attempts": len(attempt_rows),
            "stop_reason": stop_reason,
            "drain": drain,
            "requests": request_rows,
            "attempts": attempt_rows,
        }, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    # Shadow OFF immediately after official set (before analysis reruns).
    os.environ["MULTI_SHADOW_ENABLED"] = "false"
    os.environ.pop("MULTI_VERIFIER_CAPTURE_DIR", None)
    os.environ["MULTI_VERIFIER_CAPTURE_ENABLED"] = "false"
    cfg_off = reload_config_for_tests()
    proof = {
        "phase": "39R",
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
            {"phase": "39R", "n": len(request_rows), "rows": request_rows},
            ensure_ascii=False, indent=2, default=str,
        ),
        encoding="utf-8",
    )
    (OUT / "attempt_observation_log.json").write_text(
        json.dumps(
            {"phase": "39R", "n": len(attempt_rows), "rows": attempt_rows},
            ensure_ascii=False, indent=2, default=str,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    run_pilot()
