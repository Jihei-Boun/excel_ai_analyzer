"""Phase 39P — Attempt-aware Shadow correctness & escalation revalidation.

Uses Phase 39O lineage + Phase 39L capture. No semantic/policy changes.
Shadow is session-only and must be returned OFF afterward.
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
from core.shadow.worker import (
    get_inflight_for_tests,
    reload_config_for_tests,
    reset_shadow_worker_for_tests,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase39p"
TEL = OUT / "telemetry"
DATA = OUT / "datasets"
CAPTURE = OUT / "verifier_captures"

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = "qwen2.5:7b"
MATERIALIZATION = "final_schema_expr_partition"


def _cases() -> list[dict[str, Any]]:
    """Fixed 15-request set. Do not mutate mid-run."""
    cases: list[dict[str, Any]] = []

    cases.append({
        "case_id": "P39P-01",
        "family": "ordinary_multi",
        "category": "A_ordinary",
        "prompt": (
            "Join orders to customers on customer_id and list order_id, "
            "customer_name, and amount."
        ),
        "files": {
            "orders.xlsx": pd.DataFrame({
                "order_id": [1, 2, 3],
                "customer_id": ["C1", "C2", "C1"],
                "amount": [50, 80, 40],
            }),
            "customers.xlsx": pd.DataFrame({
                "customer_id": ["C1", "C2"],
                "customer_name": ["Nova", "Orion"],
            }),
        },
        "note": "Ordinary join control.",
    })
    cases.append({
        "case_id": "P39P-02",
        "family": "ordinary_multi",
        "category": "A_ordinary",
        "prompt": (
            "Join shipments to hubs on hub_id, then show total packages by hub_name."
        ),
        "files": {
            "shipments.xlsx": pd.DataFrame(
                {"hub_id": ["H1", "H1", "H2"], "packages": [4, 6, 3]}
            ),
            "hubs.xlsx": pd.DataFrame(
                {"hub_id": ["H1", "H2"], "hub_name": ["East", "West"]}
            ),
        },
        "note": "Ordinary aggregate-after-join.",
    })
    cases.append({
        "case_id": "P39P-03",
        "family": "ordinary_multi",
        "category": "A_ordinary",
        "prompt": (
            "Stack the two event logs into one table with event_id, site, and count."
        ),
        "files": {
            "events_a.xlsx": pd.DataFrame(
                {"event_id": ["E1", "E2"], "site": ["A", "A"], "count": [2, 3]}
            ),
            "events_b.xlsx": pd.DataFrame(
                {"event_id": ["E3", "E4"], "site": ["B", "B"], "count": [1, 5]}
            ),
        },
        "note": "Ordinary union/stack.",
    })

    cases.append({
        "case_id": "P39P-04",
        "family": "valid_rename_join",
        "category": "B_valid_rename_join",
        "prompt": (
            "Show yard1_qty and yard2_qty for each item_id after combining "
            "the two yard inventory files."
        ),
        "files": {
            "yard1.xlsx": pd.DataFrame({"item_id": ["I1", "I2"], "qty": [11, 7]}),
            "yard2.xlsx": pd.DataFrame({"item_id": ["I1", "I2"], "qty": [9, 8]}),
        },
        "note": "Valid rename+join dual metrics.",
    })
    cases.append({
        "case_id": "P39P-05",
        "family": "valid_rename_join",
        "category": "B_valid_rename_join",
        "prompt": (
            "Show dock_a_stock and dock_b_stock for each sku after combining "
            "the two dock inventory files."
        ),
        "files": {
            "dock_a.xlsx": pd.DataFrame({"sku": ["S1", "S2"], "stock": [14, 6]}),
            "dock_b.xlsx": pd.DataFrame({"sku": ["S1", "S2"], "stock": [12, 10]}),
        },
        "note": "Valid rename+join (historical FF-family shape, new data).",
    })
    cases.append({
        "case_id": "P39P-06",
        "family": "valid_rename_join",
        "category": "B_valid_rename_join",
        "prompt": (
            "Show bay_north_kwh and bay_south_kwh for each panel_id after "
            "combining the two bay energy files."
        ),
        "files": {
            "bay_north.xlsx": pd.DataFrame({"panel_id": ["N1", "N2"], "kwh": [5.0, 6.5]}),
            "bay_south.xlsx": pd.DataFrame({"panel_id": ["N1", "N2"], "kwh": [4.5, 7.0]}),
        },
        "note": "Valid energy dual rename+join.",
    })
    cases.append({
        "case_id": "P39P-07",
        "family": "valid_rename_join",
        "category": "B_valid_rename_join",
        "prompt": (
            "Show store_m_sales and store_n_sales for each product_id after "
            "combining the two store sales files."
        ),
        "files": {
            "store_m.xlsx": pd.DataFrame({"product_id": ["R1", "R2"], "sales": [100, 80]}),
            "store_n.xlsx": pd.DataFrame({"product_id": ["R1", "R2"], "sales": [90, 95]}),
        },
        "note": "Additional valid rename+join.",
    })

    cases.append({
        "case_id": "P39P-08",
        "family": "fake_dual",
        "category": "C_fake_dual",
        "prompt": (
            "Compare window W1 versus window W2 energy totals by node_id and "
            "keep both window totals visible."
        ),
        "files": {
            "energy_all.xlsx": pd.DataFrame({
                "node_id": ["N1", "N1", "N2", "N2"],
                "window": ["W1", "W2", "W1", "W2"],
                "kwh": [10, 12, 8, 9],
            }),
        },
        "family_ref": "P39G-11",
        "note": "Fake-dual / identical-population pressure (P39G-11-like).",
        "escalation_interest": True,
    })
    cases.append({
        "case_id": "P39P-09",
        "family": "fake_dual",
        "category": "C_fake_dual",
        "prompt": (
            "Compare shift AM versus shift PM output by line_id and keep both "
            "shift totals visible."
        ),
        "files": {
            "line_output.xlsx": pd.DataFrame({
                "line_id": ["L1", "L1", "L2", "L2"],
                "shift": ["AM", "PM", "AM", "PM"],
                "output": [40, 42, 35, 38],
            }),
        },
        "note": "Second fake-dual pressure case.",
        "escalation_interest": True,
    })
    cases.append({
        "case_id": "P39P-10",
        "family": "fake_dual",
        "category": "C_fake_dual",
        "prompt": (
            "Compare region East versus region West demand by sku_id and keep "
            "both region totals visible."
        ),
        "files": {
            "demand_all.xlsx": pd.DataFrame({
                "sku_id": ["K1", "K1", "K2", "K2"],
                "region": ["East", "West", "East", "West"],
                "demand": [7, 9, 5, 6],
            }),
        },
        "note": "Third fake-dual pressure case.",
        "escalation_interest": True,
    })

    cases.append({
        "case_id": "P39P-11",
        "family": "same_origin_partitioned",
        "category": "D_same_origin_partitioned",
        "prompt": (
            "Using the meter readings, compare day D1 versus day D2 totals by "
            "meter_id and keep both day totals visible."
        ),
        "files": {
            "meter_readings.xlsx": pd.DataFrame({
                "meter_id": ["M1", "M1", "M2", "M2"],
                "day": ["D1", "D2", "D1", "D2"],
                "kwh": [3, 4, 5, 6],
            }),
            "meters.xlsx": pd.DataFrame(
                {"meter_id": ["M1", "M2"], "building": ["B1", "B2"]}
            ),
        },
        "note": "Same-origin partitioned valid comparison (39H principle).",
    })
    cases.append({
        "case_id": "P39P-12",
        "family": "same_origin_partitioned",
        "category": "D_same_origin_partitioned",
        "prompt": (
            "Using plant readings, compare shift S1 versus shift S2 totals by "
            "machine_id and keep both shift totals visible."
        ),
        "files": {
            "plant_readings.xlsx": pd.DataFrame({
                "machine_id": ["X1", "X1", "X2", "X2"],
                "shift": ["S1", "S2", "S1", "S2"],
                "units": [10, 12, 8, 11],
            }),
            "machines.xlsx": pd.DataFrame(
                {"machine_id": ["X1", "X2"], "line": ["L1", "L2"]}
            ),
        },
        "note": "Second same-origin partitioned valid comparison.",
    })

    cases.append({
        "case_id": "P39P-13",
        "family": "escalation_pressure",
        "category": "E_escalation_pressure",
        "prompt": (
            "Show left_stock and right_stock side by side for each part_id from "
            "the two warehouse extracts. Do not collapse into one total."
        ),
        "files": {
            "wh_left.xlsx": pd.DataFrame(
                {"part_id": ["P1", "P2", "P3"], "stock": [10, 0, 4]}
            ),
            "wh_right.xlsx": pd.DataFrame(
                {"part_id": ["P1", "P2", "P4"], "stock": [8, 5, 2]}
            ),
        },
        "note": "Asymmetric keys; dual side preservation pressure.",
        "escalation_interest": True,
    })
    cases.append({
        "case_id": "P39P-14",
        "family": "escalation_pressure",
        "category": "E_escalation_pressure",
        "prompt": (
            "Combine site_a and site_b capacity tables so each zone_id has "
            "site_a_cap and site_b_cap columns visible together."
        ),
        "files": {
            "site_a.xlsx": pd.DataFrame({"zone_id": ["Z1", "Z2"], "cap": [100, 120]}),
            "site_b.xlsx": pd.DataFrame({"zone_id": ["Z1", "Z2"], "cap": [90, 110]}),
        },
        "note": "Dual-capacity rename+join pressure.",
        "escalation_interest": True,
    })

    cases.append({
        "case_id": "P39P-15",
        "family": "ambiguous",
        "category": "F_ambiguous",
        "prompt": "Do the right multi-file analysis for these spreadsheets.",
        "files": {
            "alpha.xlsx": pd.DataFrame({"id": [1], "v": [1]}),
            "beta.xlsx": pd.DataFrame({"id": [1], "w": [2]}),
        },
        "note": "Ambiguous prompt; cannot-plan / refusal OK.",
    })
    return cases


def _all_new_records(before_files: dict[str, int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted(TEL.glob("shadow_*.jsonl")):
        prev = before_files.get(str(p), 0)
        if not p.exists():
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        for line in lines[prev:]:
            if line.strip():
                out.append(json.loads(line))
        before_files[str(p)] = len(lines)
    return out


def _await_shadow_record(
    before_files: dict[str, int], *, timeout_s: float = 1800.0
) -> list[dict[str, Any]]:
    t0 = time.time()
    last_log = 0.0
    while time.time() - t0 < timeout_s:
        got = _all_new_records(before_files)
        if got:
            time.sleep(0.8)
            more = _all_new_records(before_files)
            for _ in range(30):
                if get_inflight_for_tests() <= 0:
                    break
                time.sleep(0.2)
            return got + more
        n = get_inflight_for_tests()
        now = time.time()
        if now - last_log > 30:
            print(
                f"  …waiting shadow record inflight={n} "
                f"elapsed={round(now - t0, 1)}s",
                flush=True,
            )
            last_log = now
        if n <= 0 and (now - t0) > 12.0:
            time.sleep(1.5)
            got = _all_new_records(before_files)
            if got:
                return got
            if get_inflight_for_tests() <= 0:
                return []
        time.sleep(1.0)
    print(
        f"SHADOW RECORD WAIT TIMEOUT after {timeout_s}s "
        f"(inflight={get_inflight_for_tests()})",
        flush=True,
    )
    return _all_new_records(before_files)


def _capture_lines_after(path: Path, prev_n: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[prev_n:]:
        if line.strip():
            out.append(json.loads(line))
    return out


def _ops_summary(plan: Any) -> list[str] | None:
    if not isinstance(plan, dict):
        return None
    ops = []
    for s in plan.get("steps") or []:
        if isinstance(s, dict) and s.get("op"):
            ops.append(str(s["op"]))
    return ops or None


def _attempts_from_shadow(
    shadow: dict[str, Any], captures: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    lineage = shadow.get("attempt_lineage") or {}
    attempts = list(lineage.get("attempts") or [])
    final_id = shadow.get("final_attempt_id") or lineage.get("final_attempt_id")
    verified_id = shadow.get("verified_attempt_id")

    cap_by_attempt: dict[str, list[dict[str, Any]]] = {}
    for cap in captures:
        aid = cap.get("attempt_id")
        if aid:
            cap_by_attempt.setdefault(str(aid), []).append(cap)

    rows: list[dict[str, Any]] = []
    if attempts:
        for att in attempts:
            aid = att.get("attempt_id")
            caps = cap_by_attempt.get(str(aid), [])
            primary = caps[-1] if caps else None
            rows.append({
                "attempt_id": aid,
                "parent_attempt_id": att.get("parent_attempt_id"),
                "attempt_type": att.get("stage") or att.get("attempt_stage"),
                "planner_model": att.get("planner_model"),
                "planner_path": att.get("planner_path"),
                "plan_fingerprint": att.get("plan_fingerprint"),
                "result_fingerprint": att.get("result_fingerprint"),
                "escalation_trigger": att.get("escalation_trigger"),
                "attempt_disposition": att.get("disposition")
                or att.get("attempt_disposition"),
                "became_final": (
                    True if final_id and aid == final_id else att.get("became_final")
                ),
                "is_verified_attempt": bool(verified_id and aid == verified_id),
                "is_final_attempt": bool(final_id and aid == final_id),
                "verifier_invocation_ids": [
                    c.get("verifier_invocation_id") for c in caps
                ],
                "verifier_invocations": [{
                    "verifier_invocation_id": c.get("verifier_invocation_id"),
                    "verdict": c.get("parsed_verdict"),
                    "reason_code": c.get("parsed_reason_code"),
                    "plan_fingerprint": c.get("plan_fingerprint"),
                    "exact_payload_hash": c.get("exact_payload_hash"),
                    "attempt_id": c.get("attempt_id"),
                } for c in caps],
                "primary_verifier_invocation_id": (primary or {}).get(
                    "verifier_invocation_id"
                ),
                "primary_verifier_verdict": (primary or {}).get("parsed_verdict"),
                "primary_verifier_reason": (primary or {}).get("parsed_reason_code"),
                "attempt_manual_correct": None,
                "verdict_correctness": None,
                "claim_quality": None,
                "classification_32b": None,
                "notes_ko": None,
                "lineage_incomplete": False,
            })
        return rows

    if shadow.get("semantic_verifier_invoked") or captures:
        primary = captures[-1] if captures else None
        rows.append({
            "attempt_id": verified_id or (primary or {}).get("attempt_id"),
            "parent_attempt_id": (primary or {}).get("parent_attempt_id"),
            "attempt_type": "unknown_missing_lineage",
            "planner_model": None,
            "planner_path": None,
            "plan_fingerprint": shadow.get("verified_plan_fingerprint")
            or (primary or {}).get("plan_fingerprint"),
            "result_fingerprint": shadow.get("result_fingerprint"),
            "escalation_trigger": (primary or {}).get("escalation_trigger"),
            "attempt_disposition": None,
            "became_final": True,
            "is_verified_attempt": True,
            "is_final_attempt": True,
            "verifier_invocation_ids": [
                c.get("verifier_invocation_id") for c in captures
            ],
            "verifier_invocations": [{
                "verifier_invocation_id": c.get("verifier_invocation_id"),
                "verdict": c.get("parsed_verdict"),
                "reason_code": c.get("parsed_reason_code"),
                "plan_fingerprint": c.get("plan_fingerprint"),
                "exact_payload_hash": c.get("exact_payload_hash"),
                "attempt_id": c.get("attempt_id"),
            } for c in captures],
            "primary_verifier_invocation_id": (primary or {}).get(
                "verifier_invocation_id"
            ),
            "primary_verifier_verdict": (primary or {}).get("parsed_verdict")
            or shadow.get("semantic_verifier_verdict"),
            "primary_verifier_reason": (primary or {}).get("parsed_reason_code")
            or shadow.get("semantic_verifier_reason"),
            "attempt_manual_correct": None,
            "verdict_correctness": None,
            "claim_quality": None,
            "classification_32b": None,
            "notes_ko": "lineage_missing_or_incomplete",
            "lineage_incomplete": True,
        })
    return rows


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
        flush=True,
    )
    if not cfg.enabled:
        raise RuntimeError("Shadow failed to enable for session")

    cases = _cases()
    request_set = [{
        "case_id": c["case_id"],
        "family": c["family"],
        "category": c["category"],
        "prompt": c["prompt"],
        "n_files": len(c["files"]),
        "file_names": list(c["files"]),
        "family_ref": c.get("family_ref"),
        "escalation_interest": bool(c.get("escalation_interest")),
        "note": c.get("note"),
    } for c in cases]
    (OUT / "pilot_request_set.json").write_text(
        json.dumps({
            "phase": "39P",
            "n": len(request_set),
            "frozen": True,
            "family_counts": dict(Counter(c["family"] for c in cases)),
            "category_counts": dict(Counter(c["category"] for c in cases)),
            "cases": request_set,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    file_line_pos: dict[str, int] = {}
    request_rows: list[dict[str, Any]] = []
    attempt_rows: list[dict[str, Any]] = []
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    capture_path = CAPTURE / f"verifier_invocations_{day}.jsonl"
    stop_reason: str | None = None

    for i, case in enumerate(cases, 1):
        cid = case["case_id"]
        rid = f"req-{i:02d}"
        print(f"\n=== [{i}/{len(cases)}] {cid} {case['family']} ===", flush=True)
        case_dir = DATA / cid
        case_dir.mkdir(parents=True, exist_ok=True)
        named: list[tuple[str, pd.DataFrame]] = []
        for name, df in case["files"].items():
            df.to_excel(case_dir / name, index=False)
            named.append((name, df.copy()))

        os.environ["MULTI_VERIFIER_CAPTURE_CASE_ID"] = cid
        os.environ["MULTI_VERIFIER_CAPTURE_REQUEST_ID"] = rid

        for p in TEL.glob("shadow_*.jsonl"):
            file_line_pos[str(p)] = len(p.read_text(encoding="utf-8").splitlines())
        capture_prev = (
            len(capture_path.read_text(encoding="utf-8").splitlines())
            if capture_path.exists() else 0
        )

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
            )
        except Exception as exc:  # noqa: BLE001
            legacy_err = f"{type(exc).__name__}: {exc}"
            print("LEGACY EXCEPTION", legacy_err, flush=True)

        legacy_latency = round(time.time() - t0, 3)
        legacy_reply = getattr(outcome, "reply", None) if outcome else None
        legacy_op = getattr(outcome, "operation_name", None) if outcome else None
        legacy_df = getattr(outcome, "dataframe", None) if outcome else None

        new_recs = _await_shadow_record(file_line_pos, timeout_s=1800.0)
        shadow_rec = new_recs[-1] if new_recs else None
        shadow = (shadow_rec or {}).get("shadow") or {}
        legacy_tel = (shadow_rec or {}).get("legacy") or {}

        time.sleep(0.5)
        new_caps = _capture_lines_after(capture_path, capture_prev)
        case_caps = [c for c in new_caps if c.get("case_id") == cid] or new_caps

        att_rows = _attempts_from_shadow(shadow, case_caps)
        for ar in att_rows:
            ar["request_id"] = rid
            ar["case_id"] = cid
            ar["family"] = case["family"]
            ar["category"] = case["category"]
            ar["phase"] = "39P"
            attempt_rows.append(ar)

        lineage = shadow.get("attempt_lineage") or {}
        final_attempt_id = shadow.get("final_attempt_id") or lineage.get("final_attempt_id")
        verified_attempt_id = shadow.get("verified_attempt_id")
        lineage_complete = bool(lineage.get("attempts") and final_attempt_id)
        attribution_ok = True
        if case_caps:
            for c in case_caps:
                if not c.get("attempt_id"):
                    attribution_ok = False
        ambiguous_final = False
        if lineage.get("attempts") and final_attempt_id:
            final_ids = {
                a.get("attempt_id")
                for a in lineage["attempts"]
                if a.get("became_final") or a.get("disposition") == "final"
            }
            if final_attempt_id:
                final_ids.add(final_attempt_id)
            # more than one distinct final candidate is ambiguous
            if len([x for x in final_ids if x]) > 1 and len(
                {a.get("attempt_id") for a in lineage["attempts"]
                 if a.get("became_final") is True}
            ) > 1:
                ambiguous_final = True

        row = {
            "request_id": rid,
            "case_id": cid,
            "phase": "39P",
            "candidate_version": "Phase39O-lineage+V2.2",
            "materialization_mode": MATERIALIZATION,
            "family": case["family"],
            "category": case["category"],
            "family_ref": case.get("family_ref"),
            "prompt": case["prompt"],
            "files": list(case["files"]),
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
            "shadow_n_new_records": len(new_recs),
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
            "lineage_complete": lineage_complete,
            "attribution_ok": attribution_ok,
            "ambiguous_final_attempt": ambiguous_final,
            "n_attempts": len(att_rows),
            "n_verifier_captures": len(case_caps),
            "capture_invocation_ids": [
                c.get("verifier_invocation_id") for c in case_caps
            ],
            "comparison": (shadow_rec or {}).get("comparison"),
            "shadow_error_family": shadow.get("error_family"),
            "shadow_error_message": shadow.get("error_message"),
            "model_calls": shadow.get("model_calls"),
            "raw_shadow_record": shadow_rec,
            "legacy_correct": None,
            "final_shadow_correct": None,
            "manual_review": None,
            "notes_ko": None,
        }
        request_rows.append(row)
        (OUT / "request_observation_log.json").write_text(
            json.dumps({"phase": "39P", "n": len(request_rows), "rows": request_rows},
                       ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (OUT / "attempt_observation_log.json").write_text(
            json.dumps({"phase": "39P", "n": len(attempt_rows), "rows": attempt_rows},
                       ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(
            "legacy", row["legacy_status"],
            "shadow", row["shadow_recorded"], row["shadow_status"],
            "verdict", row["verifier_verdict"],
            "final_attempt", (final_attempt_id or "")[:28],
            "attempts", len(att_rows),
            "caps", len(case_caps),
            "lineage_ok", lineage_complete and attribution_ok,
            flush=True,
        )

        if ambiguous_final:
            stop_reason = f"STOP-5 ambiguous final attempt on {cid}"
            print(stop_reason, flush=True)
            break
        if shadow.get("semantic_verifier_invoked") and case_caps and not attribution_ok:
            stop_reason = f"STOP-4 capture missing attempt_id on {cid}"
            print(stop_reason, flush=True)
            break

    # Capture index
    cap_index = []
    for p in sorted(CAPTURE.glob("verifier_invocations_*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            cap_index.append({
                "verifier_invocation_id": c.get("verifier_invocation_id"),
                "request_id": c.get("request_id"),
                "case_id": c.get("case_id"),
                "attempt_id": c.get("attempt_id"),
                "plan_fingerprint": c.get("plan_fingerprint"),
                "parsed_verdict": c.get("parsed_verdict"),
                "parsed_reason_code": c.get("parsed_reason_code"),
                "exact_payload_hash": c.get("exact_payload_hash"),
                "became_final": c.get("became_final"),
                "final_attempt_id": c.get("final_attempt_id"),
                "parent_attempt_id": c.get("parent_attempt_id"),
                "escalation_trigger": c.get("escalation_trigger"),
            })
    (OUT / "verifier_capture_index.json").write_text(
        json.dumps({"phase": "39P", "n": len(cap_index), "rows": cap_index}, indent=2),
        encoding="utf-8",
    )

    report = {
        "phase": "39P",
        "candidate": "Phase39O-lineage+V2.2",
        "materialization_mode": MATERIALIZATION,
        "n_requests": len(request_rows),
        "n_attempts": len(attempt_rows),
        "shadow_recorded": sum(1 for r in request_rows if r["shadow_recorded"]),
        "lineage_complete": sum(1 for r in request_rows if r["lineage_complete"]),
        "attribution_ok": sum(1 for r in request_rows if r["attribution_ok"]),
        "stop_reason": stop_reason,
        "requests": request_rows,
        "attempts": attempt_rows,
    }
    (OUT / "observation_bundle.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    os.environ["MULTI_SHADOW_ENABLED"] = "false"
    os.environ.pop("MULTI_VERIFIER_CAPTURE_DIR", None)
    os.environ["MULTI_VERIFIER_CAPTURE_ENABLED"] = "false"
    cfg_off = reload_config_for_tests()
    proof = {
        "phase": "39P",
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
    return report


if __name__ == "__main__":
    run_pilot()
