"""Phase 39M — Focused live Shadow observation with exact verifier capture/replay.

Frozen candidate: Independent Verifier + final_schema_expr_partition (V2.2).
Uses Phase 39L capture instrumentation. No mid-run semantic changes.
Legacy remains user-visible. Shadow session-only, then returned OFF.
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
OUT = ROOT / "benchmark_results/multi/phase39m"
TEL = OUT / "telemetry"
DATA = OUT / "datasets"
CAPTURE = OUT / "verifier_captures"

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = "qwen2.5:7b"
MATERIALIZATION = "final_schema_expr_partition"


def _cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    cases.append(
        {
            "case_id": "P39M-01",
            "family": "ordinary_multi",
            "category": "A_ordinary_join",
            "prompt": (
                "Join orders to customers on customer_id and list order_id, "
                "customer_name, and amount."
            ),
            "files": {
                "orders.xlsx": pd.DataFrame(
                    {
                        "order_id": [1, 2, 3],
                        "customer_id": ["C1", "C2", "C1"],
                        "amount": [50, 80, 40],
                    }
                ),
                "customers.xlsx": pd.DataFrame(
                    {
                        "customer_id": ["C1", "C2"],
                        "customer_name": ["Nova", "Orion"],
                    }
                ),
            },
            "anchor_type": "new",
            "note": "Ordinary join control.",
        }
    )
    cases.append(
        {
            "case_id": "P39M-02",
            "family": "ordinary_multi",
            "category": "A_ordinary_agg",
            "prompt": (
                "Join shipments to hubs on hub_id, then show total packages "
                "by hub_name."
            ),
            "files": {
                "shipments.xlsx": pd.DataFrame(
                    {"hub_id": ["H1", "H1", "H2"], "packages": [4, 6, 3]}
                ),
                "hubs.xlsx": pd.DataFrame(
                    {"hub_id": ["H1", "H2"], "hub_name": ["East", "West"]}
                ),
            },
            "anchor_type": "new",
            "note": "Ordinary aggregate-after-join control.",
        }
    )
    cases.append(
        {
            "case_id": "P39M-03",
            "family": "ordinary_multi",
            "category": "A_ordinary_union",
            "prompt": (
                "Stack the two event logs into one table with event_id, site, "
                "and count."
            ),
            "files": {
                "events_a.xlsx": pd.DataFrame(
                    {
                        "event_id": ["E1", "E2"],
                        "site": ["A", "A"],
                        "count": [2, 3],
                    }
                ),
                "events_b.xlsx": pd.DataFrame(
                    {
                        "event_id": ["E3", "E4"],
                        "site": ["B", "B"],
                        "count": [1, 5],
                    }
                ),
            },
            "anchor_type": "new",
            "note": "Ordinary union/stack control.",
        }
    )
    cases.append(
        {
            "case_id": "P39M-04",
            "family": "valid_rename_join",
            "category": "B_valid_rename_join",
            "prompt": (
                "Show yard1_qty and yard2_qty for each item_id after combining "
                "the two yard inventory files."
            ),
            "files": {
                "yard1.xlsx": pd.DataFrame(
                    {"item_id": ["I1", "I2"], "qty": [11, 7]}
                ),
                "yard2.xlsx": pd.DataFrame(
                    {"item_id": ["I1", "I2"], "qty": [9, 8]}
                ),
            },
            "anchor_type": "structural_family_equivalent",
            "family_ref": "P39J-05/06 rename+join",
            "note": "Valid rename+join dual metrics preserved.",
        }
    )
    cases.append(
        {
            "case_id": "P39M-05",
            "family": "valid_rename_join",
            "category": "B_valid_rename_join",
            "prompt": (
                "Show dock_a_stock and dock_b_stock for each sku after "
                "combining the two dock inventory files."
            ),
            "files": {
                "dock_a.xlsx": pd.DataFrame(
                    {"sku": ["S1", "S2"], "stock": [14, 6]}
                ),
                "dock_b.xlsx": pd.DataFrame(
                    {"sku": ["S1", "S2"], "stock": [12, 10]}
                ),
            },
            "anchor_type": "structural_family_equivalent",
            "family_ref": "P39J-06",
            "note": "Closest structural equivalent to historical P39J-06 FF family.",
        }
    )
    cases.append(
        {
            "case_id": "P39M-06",
            "family": "valid_rename_join",
            "category": "B_valid_rename_join",
            "prompt": (
                "Show bay_north_kwh and bay_south_kwh for each panel_id after "
                "combining the two bay energy files."
            ),
            "files": {
                "bay_north.xlsx": pd.DataFrame(
                    {"panel_id": ["N1", "N2"], "kwh": [5.0, 6.5]}
                ),
                "bay_south.xlsx": pd.DataFrame(
                    {"panel_id": ["N1", "N2"], "kwh": [4.5, 7.0]}
                ),
            },
            "anchor_type": "structural_family_equivalent",
            "family_ref": "P39J-07",
            "note": "Rename+join energy dual analogous to P39J-07 family.",
        }
    )
    cases.append(
        {
            "case_id": "P39M-07",
            "family": "valid_rename_join",
            "category": "B_valid_rename_join",
            "prompt": (
                "Show lane_x_cars and lane_y_cars for each gate_id after "
                "combining the two lane count files."
            ),
            "files": {
                "lane_x.xlsx": pd.DataFrame(
                    {"gate_id": ["G1", "G2"], "cars": [20, 18]}
                ),
                "lane_y.xlsx": pd.DataFrame(
                    {"gate_id": ["G1", "G2"], "cars": [22, 15]}
                ),
            },
            "anchor_type": "structural_family_equivalent",
            "note": "Additional valid rename+join exposure.",
        }
    )
    cases.append(
        {
            "case_id": "P39M-08",
            "family": "valid_rename_join",
            "category": "B_valid_rename_join",
            "prompt": (
                "Show store_m_sales and store_n_sales for each product_id after "
                "combining the two store sales files."
            ),
            "files": {
                "store_m.xlsx": pd.DataFrame(
                    {"product_id": ["R1", "R2"], "sales": [100, 80]}
                ),
                "store_n.xlsx": pd.DataFrame(
                    {"product_id": ["R1", "R2"], "sales": [90, 95]}
                ),
            },
            "anchor_type": "structural_family_equivalent",
            "note": "Additional valid rename+join exposure.",
        }
    )
    cases.append(
        {
            "case_id": "P39M-09",
            "family": "valid_rename_join",
            "category": "B_valid_rename_join",
            "prompt": (
                "Show tank_p_liters and tank_q_liters for each fluid_id after "
                "combining the two tank volume files."
            ),
            "files": {
                "tank_p.xlsx": pd.DataFrame(
                    {"fluid_id": ["F1", "F2"], "liters": [30, 25]}
                ),
                "tank_q.xlsx": pd.DataFrame(
                    {"fluid_id": ["F1", "F2"], "liters": [28, 27]}
                ),
            },
            "anchor_type": "structural_family_equivalent",
            "note": "Additional valid rename+join exposure.",
        }
    )
    cases.append(
        {
            "case_id": "P39M-10",
            "family": "valid_rename_join",
            "category": "C_historical_family_anchor",
            "prompt": (
                "Show lot_a_price and lot_b_price for each part_id after "
                "combining the two lot price files."
            ),
            "files": {
                "lot_a.xlsx": pd.DataFrame(
                    {"part_id": ["P1", "P2"], "price": [3.0, 4.0]}
                ),
                "lot_b.xlsx": pd.DataFrame(
                    {"part_id": ["P1", "P2"], "price": [3.5, 3.8]}
                ),
            },
            "anchor_type": "structural_family_equivalent",
            "family_ref": "P39J-05 / P39I-09",
            "note": "Historical rename+join anchor shape with new filenames.",
        }
    )
    cases.append(
        {
            "case_id": "P39M-11",
            "family": "fake_dual",
            "category": "D_fake_dual",
            "prompt": (
                "Compare window W1 versus window W2 energy totals by node_id "
                "and keep both window totals visible."
            ),
            "files": {
                "energy_all.xlsx": pd.DataFrame(
                    {
                        "node_id": ["N1", "N1", "N2", "N2"],
                        "window": ["W1", "W2", "W1", "W2"],
                        "kwh": [10, 12, 8, 9],
                    }
                ),
            },
            "anchor_type": "structural_family_equivalent",
            "family_ref": "P39G-11",
            "expected_verifier_not": "pass",
            "note": "Fake-dual / identical-population pressure control.",
        }
    )
    cases.append(
        {
            "case_id": "P39M-12",
            "family": "fake_dual",
            "category": "D_fake_dual",
            "prompt": (
                "Compare shift AM versus shift PM output by line_id and keep "
                "both shift totals visible."
            ),
            "files": {
                "line_output.xlsx": pd.DataFrame(
                    {
                        "line_id": ["L1", "L1", "L2", "L2"],
                        "shift": ["AM", "PM", "AM", "PM"],
                        "output": [40, 42, 35, 38],
                    }
                ),
            },
            "anchor_type": "new",
            "expected_verifier_not": "pass",
            "note": "Second fake-dual pressure control.",
        }
    )
    cases.append(
        {
            "case_id": "P39M-13",
            "family": "same_origin_partitioned",
            "category": "E_same_origin_partitioned",
            "prompt": (
                "Using the meter readings, compare day D1 versus day D2 totals "
                "by meter_id and keep both day totals visible."
            ),
            "files": {
                "meter_readings.xlsx": pd.DataFrame(
                    {
                        "meter_id": ["M1", "M1", "M2", "M2"],
                        "day": ["D1", "D2", "D1", "D2"],
                        "kwh": [3, 4, 5, 6],
                    }
                ),
                "meters.xlsx": pd.DataFrame(
                    {
                        "meter_id": ["M1", "M2"],
                        "building": ["B1", "B2"],
                    }
                ),
            },
            "anchor_type": "structural_family_equivalent",
            "note": "Same-origin partitioned valid comparison; may timeout.",
        }
    )
    cases.append(
        {
            "case_id": "P39M-14",
            "family": "ambiguous",
            "category": "F_ambiguous",
            "prompt": "Do the right multi-file analysis for these spreadsheets.",
            "files": {
                "alpha.xlsx": pd.DataFrame({"id": [1], "v": [1]}),
                "beta.xlsx": pd.DataFrame({"id": [1], "w": [2]}),
            },
            "anchor_type": "new",
            "note": "Ambiguous prompt; legitimate cannot-plan / refusal OK.",
        }
    )
    cases.append(
        {
            "case_id": "P39M-15",
            "family": "ordinary_multi",
            "category": "A_ordinary_join",
            "prompt": (
                "Join employees to departments on dept_id and list emp_id, "
                "dept_name, and title."
            ),
            "files": {
                "employees.xlsx": pd.DataFrame(
                    {
                        "emp_id": ["E1", "E2"],
                        "dept_id": ["D1", "D2"],
                        "title": ["Eng", "Ops"],
                    }
                ),
                "departments.xlsx": pd.DataFrame(
                    {
                        "dept_id": ["D1", "D2"],
                        "dept_name": ["Engineering", "Operations"],
                    }
                ),
            },
            "anchor_type": "new",
            "note": "Fourth ordinary control.",
        }
    )
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
    if str(cfg.telemetry_dir) != tel_abs:
        raise RuntimeError(
            f"telemetry dir mismatch: {cfg.telemetry_dir!r} vs {tel_abs!r}"
        )

    cases = _cases()
    request_set = [
        {
            "case_id": c["case_id"],
            "family": c["family"],
            "category": c["category"],
            "prompt": c["prompt"],
            "n_files": len(c["files"]),
            "file_names": list(c["files"]),
            "anchor_type": c.get("anchor_type"),
            "family_ref": c.get("family_ref"),
            "expected_verifier_not": c.get("expected_verifier_not"),
            "note": c.get("note"),
        }
        for c in cases
    ]
    (OUT / "pilot_request_set.json").write_text(
        json.dumps(
            {
                "phase": "39M",
                "n": len(request_set),
                "family_counts": dict(Counter(c["family"] for c in cases)),
                "cases": request_set,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    file_line_pos: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    capture_path = CAPTURE / f"verifier_invocations_{day}.jsonl"

    for i, case in enumerate(cases, 1):
        cid = case["case_id"]
        print(f"\n=== [{i}/{len(cases)}] {cid} {case['family']} ===", flush=True)
        case_dir = DATA / cid
        case_dir.mkdir(parents=True, exist_ok=True)
        named: list[tuple[str, pd.DataFrame]] = []
        for name, df in case["files"].items():
            df.to_excel(case_dir / name, index=False)
            named.append((name, df.copy()))

        os.environ["MULTI_VERIFIER_CAPTURE_CASE_ID"] = cid
        os.environ["MULTI_VERIFIER_CAPTURE_REQUEST_ID"] = f"req-{i:02d}"

        for p in TEL.glob("shadow_*.jsonl"):
            file_line_pos[str(p)] = len(
                p.read_text(encoding="utf-8").splitlines()
            )
        capture_prev = (
            len(capture_path.read_text(encoding="utf-8").splitlines())
            if capture_path.exists()
            else 0
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
        legacy_shape = (
            list(legacy_df.shape) if isinstance(legacy_df, pd.DataFrame) else None
        )
        legacy_cols = (
            list(map(str, legacy_df.columns))
            if isinstance(legacy_df, pd.DataFrame)
            else None
        )

        new_recs = _await_shadow_record(file_line_pos, timeout_s=1800.0)
        shadow_rec = new_recs[-1] if new_recs else None
        shadow = (shadow_rec or {}).get("shadow") or {}
        legacy_tel = (shadow_rec or {}).get("legacy") or {}

        time.sleep(0.5)
        new_caps = _capture_lines_after(capture_path, capture_prev)
        primary_cap = None
        for cap in reversed(new_caps):
            if not primary_cap:
                primary_cap = cap
            if cap.get("case_id") == cid:
                primary_cap = cap
                break

        row = {
            "request_id": f"req-{i:02d}",
            "case_id": cid,
            "phase": "39M",
            "candidate_version": "Phase39H-V2.2",
            "materialization_mode": MATERIALIZATION,
            "family": case["family"],
            "category": case["category"],
            "anchor_type": case.get("anchor_type"),
            "family_ref": case.get("family_ref"),
            "prompt": case["prompt"],
            "files": list(case["files"]),
            "legacy_status": (
                "exception"
                if legacy_err
                else (
                    "success"
                    if legacy_df is not None
                    or (legacy_reply and len(str(legacy_reply)) > 0)
                    else "unknown"
                )
            ),
            "legacy_operation": legacy_op,
            "legacy_reply_preview": (str(legacy_reply or ""))[:500],
            "legacy_shape": legacy_shape,
            "legacy_columns": legacy_cols,
            "legacy_latency_s": legacy_latency,
            "legacy_error": legacy_err,
            "legacy_telemetry": legacy_tel,
            "shadow_recorded": shadow_rec is not None,
            "shadow_n_new_records": len(new_recs),
            "shadow_status": shadow.get("shadow_status")
            or shadow.get("status")
            or (shadow_rec or {}).get("event"),
            "shadow_success": shadow.get("shadow_success"),
            "cannot_plan": shadow.get("cannot_plan"),
            "verifier_invoked": shadow.get("semantic_verifier_invoked"),
            "verifier_verdict": shadow.get("semantic_verifier_verdict"),
            "verifier_reason": shadow.get("semantic_verifier_reason"),
            "verifier_evidence": shadow.get("semantic_verifier_evidence"),
            "failure_32b": shadow.get("failure_32b_invoked"),
            "semantic_32b": shadow.get("semantic_32b_invoked"),
            "final_path": shadow.get("final_path"),
            "escalation_source": shadow.get("escalation_source"),
            "shadow_latency_s": shadow.get("latency_total_s"),
            "latency_by_stage_s": shadow.get("latency_by_stage_s"),
            "final_plan": shadow.get("final_plan"),
            "result_fingerprint": shadow.get("result_fingerprint"),
            "final_columns": (
                (shadow.get("result_fingerprint") or {}).get("columns")
                if isinstance(shadow.get("result_fingerprint"), dict)
                else None
            ),
            "comparison": (shadow_rec or {}).get("comparison"),
            "shadow_error_family": shadow.get("error_family"),
            "shadow_error_message": shadow.get("error_message"),
            "model_calls": shadow.get("model_calls"),
            "raw_shadow_record": shadow_rec,
            "capture_status": "captured" if primary_cap else "missing",
            "capture_n_new": len(new_caps),
            "verifier_invocation_id": (primary_cap or {}).get(
                "verifier_invocation_id"
            ),
            "exact_payload_hash": (primary_cap or {}).get("exact_payload_hash"),
            "canonical_payload_hash": (primary_cap or {}).get(
                "canonical_payload_hash"
            ),
            "prompt_version_hash": (primary_cap or {}).get("prompt_version_hash"),
            "capture_raw_response_excerpt": (
                ((primary_cap or {}).get("raw_model_response_text") or "")[:500]
            ),
            "capture_parsed_verdict": (primary_cap or {}).get("parsed_verdict"),
            "capture_parsed_reason": (primary_cap or {}).get("parsed_reason_code"),
            "capture_result_provided": (primary_cap or {}).get("result_provided"),
            "capture_model_id": (primary_cap or {}).get("model_id"),
            "capture_temperature": (primary_cap or {}).get("temperature"),
            "all_capture_invocation_ids": [
                c.get("verifier_invocation_id") for c in new_caps
            ],
            "legacy_correct": None,
            "shadow_correct": None,
            "ls_structural": None,
            "manual_review": None,
            "silent_wrong": None,
            "verifier_false_fail": None,
            "escalation_32b_class": None,
            "replay_status": None,
            "notes": None,
        }
        rows.append(row)
        (OUT / "observation_log.json").write_text(
            json.dumps(
                {
                    "phase": "39M",
                    "candidate": "Phase39H-V2.2",
                    "materialization_mode": MATERIALIZATION,
                    "n": len(rows),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        print(
            "legacy",
            row["legacy_status"],
            "shadow",
            row["shadow_recorded"],
            row["shadow_status"],
            "verdict",
            row["verifier_verdict"],
            "capture",
            row["capture_status"],
            "hash",
            (row["exact_payload_hash"] or "")[:12],
            flush=True,
        )

    report = {
        "phase": "39M",
        "candidate": "Phase39H-V2.2",
        "materialization_mode": MATERIALIZATION,
        "n": len(rows),
        "shadow_recorded": sum(1 for r in rows if r["shadow_recorded"]),
        "captures": sum(1 for r in rows if r["capture_status"] == "captured"),
        "rows": rows,
    }
    (OUT / "observation_log.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    os.environ["MULTI_SHADOW_ENABLED"] = "false"
    os.environ.pop("MULTI_VERIFIER_CAPTURE_DIR", None)
    os.environ["MULTI_VERIFIER_CAPTURE_ENABLED"] = "false"
    cfg_off = reload_config_for_tests()
    proof = {
        "phase": "39M",
        "MULTI_SHADOW_ENABLED_env": os.environ.get("MULTI_SHADOW_ENABLED"),
        "config_enabled": bool(cfg_off.enabled),
        "confirmed_off": not bool(cfg_off.enabled),
        "MULTI_VERIFIER_CAPTURE_ENABLED_env": os.environ.get(
            "MULTI_VERIFIER_CAPTURE_ENABLED"
        ),
        "confirmed_at_utc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }
    (OUT / "shadow_off_proof.json").write_text(
        json.dumps(proof, indent=2), encoding="utf-8"
    )
    print("\nShadow returned to OFF", proof, flush=True)
    return report


if __name__ == "__main__":
    run_pilot()
