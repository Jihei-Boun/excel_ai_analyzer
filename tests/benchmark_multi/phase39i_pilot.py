"""Phase 39I — V2.2 limited Shadow re-observation (measure-only).

Frozen candidate: Independent Verifier + final_schema_expr_partition (V2.2).
Legacy remains user-visible. Shadow session-only, fire-and-forget.
NO mid-run candidate changes.
"""

from __future__ import annotations

import json
import os
import time
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
OUT = ROOT / "benchmark_results/multi/phase39i"
TEL = OUT / "telemetry"
DATA = OUT / "datasets"

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = "qwen2.5:7b"


def _cases() -> list[dict[str, Any]]:
    """13 production-like requests; new wording vs Phase 39G; mixed families."""
    cases: list[dict[str, Any]] = []

    # A. Normal multi-file integration — 2
    cases.append(
        {
            "case_id": "P39I-01",
            "category": "A_normal_integration",
            "prompt": (
                "Combine orders with customer names on customer_id and list "
                "order_id, customer_name, and amount."
            ),
            "files": {
                "orders.xlsx": pd.DataFrame(
                    {
                        "order_id": [501, 502, 503],
                        "customer_id": ["C1", "C2", "C1"],
                        "amount": [120, 80, 45],
                    }
                ),
                "customers.xlsx": pd.DataFrame(
                    {
                        "customer_id": ["C1", "C2"],
                        "customer_name": ["Ada", "Ben"],
                    }
                ),
            },
        }
    )
    cases.append(
        {
            "case_id": "P39I-02",
            "category": "A_normal_integration",
            "prompt": "Stack the two compatible shipment logs into one detail table.",
            "files": {
                "ship_east.xlsx": pd.DataFrame(
                    {"shipment_id": ["S1", "S2"], "kg": [10.0, 12.5]}
                ),
                "ship_west.xlsx": pd.DataFrame(
                    {"shipment_id": ["S3", "S4"], "kg": [9.0, 11.0]}
                ),
            },
        }
    )

    # B. Aggregate after integration — 2
    cases.append(
        {
            "case_id": "P39I-03",
            "category": "B_agg_after_integration",
            "prompt": (
                "Join sales to regions on region_id, then show total sales_amount "
                "by region_name."
            ),
            "files": {
                "sales.xlsx": pd.DataFrame(
                    {
                        "region_id": ["R1", "R1", "R2"],
                        "sales_amount": [100, 50, 70],
                    }
                ),
                "regions.xlsx": pd.DataFrame(
                    {
                        "region_id": ["R1", "R2"],
                        "region_name": ["North", "South"],
                    }
                ),
            },
        }
    )
    cases.append(
        {
            "case_id": "P39I-04",
            "category": "B_agg_after_integration",
            "prompt": (
                "Put both plant logs together, keep only rows with status ok, "
                "then total quantity by plant_id."
            ),
            "files": {
                "plant_a_log.xlsx": pd.DataFrame(
                    {
                        "plant_id": ["P1", "P1", "P2"],
                        "status": ["ok", "bad", "ok"],
                        "quantity": [5, 3, 8],
                    }
                ),
                "plant_b_log.xlsx": pd.DataFrame(
                    {
                        "plant_id": ["P2", "P3"],
                        "status": ["ok", "ok"],
                        "quantity": [4, 6],
                    }
                ),
            },
        }
    )

    # C. Genuine dual-side comparison — 3 (includes same-origin partitioned)
    cases.append(
        {
            "case_id": "P39I-05",
            "category": "C_genuine_dual_rename_join",
            "prompt": (
                "Compare shift A and shift B output by machine_id and keep both "
                "shift values visible."
            ),
            "files": {
                "shift_a.xlsx": pd.DataFrame(
                    {"machine_id": ["M1", "M2"], "output": [40, 35]}
                ),
                "shift_b.xlsx": pd.DataFrame(
                    {"machine_id": ["M1", "M2"], "output": [42, 30]}
                ),
            },
        }
    )
    cases.append(
        {
            "case_id": "P39I-06",
            "category": "C_genuine_dual_agg_join",
            "prompt": (
                "For each site_id, show total hours from the day roster and from "
                "the night roster side by side."
            ),
            "files": {
                "day_roster.xlsx": pd.DataFrame(
                    {
                        "site_id": ["T1", "T1", "T2"],
                        "hours": [4, 3, 5],
                    }
                ),
                "night_roster.xlsx": pd.DataFrame(
                    {
                        "site_id": ["T1", "T2", "T2"],
                        "hours": [2, 6, 1],
                    }
                ),
            },
        }
    )
    cases.append(
        {
            "case_id": "P39I-07",
            "category": "C_genuine_same_origin_partitioned",
            "prompt": (
                "Using the transaction history, compare period P1 versus period P2 "
                "totals by account_id and keep both period totals visible. "
                "Use the account list to keep accounts aligned."
            ),
            "files": {
                "tx_history.xlsx": pd.DataFrame(
                    {
                        "account_id": ["A1", "A1", "A2", "A2", "A1", "A2"],
                        "period": ["P1", "P2", "P1", "P2", "P1", "P2"],
                        "amount": [10, 12, 20, 18, 5, 7],
                    }
                ),
                "accounts.xlsx": pd.DataFrame(
                    {"account_id": ["A1", "A2"], "account_name": ["Cash", "Receivable"]}
                ),
            },
            "review_focus": "same_origin_partitioned_dual",
        }
    )

    # D. Fake dual-side pressure — 2
    cases.append(
        {
            "case_id": "P39I-08",
            "category": "D_fake_dual_pressure",
            "prompt": (
                "Compare window X and window Y energy by meter_id and keep both "
                "window totals visible."
            ),
            "files": {
                "window_x.xlsx": pd.DataFrame(
                    {"meter_id": ["K1", "K2"], "kwh": [15, 25]}
                ),
                "window_y.xlsx": pd.DataFrame(
                    {"meter_id": ["K1", "K2"], "kwh": [16, 22]}
                ),
            },
            "review_focus": "fake_dual_if_union_double_alias",
            "note": (
                "Natural compare prompt; planner may produce valid dual join OR "
                "union+double-alias fake dual. Review lineage."
            ),
        }
    )
    cases.append(
        {
            "case_id": "P39I-09",
            "category": "D_fake_dual_pressure",
            "prompt": (
                "Show batch1_cost and batch2_cost for each sku_id after combining "
                "the two batch files."
            ),
            "files": {
                "batch1.xlsx": pd.DataFrame(
                    {"sku_id": ["U1", "U2"], "cost": [3.0, 4.0]}
                ),
                "batch2.xlsx": pd.DataFrame(
                    {"sku_id": ["U1", "U2"], "cost": [3.5, 3.8]}
                ),
            },
            "review_focus": "fake_dual_if_union_double_alias",
        }
    )

    # E. Cannot-plan / ambiguous — 1
    cases.append(
        {
            "case_id": "P39I-10",
            "category": "E_cannot_plan_ambiguous",
            "prompt": (
                "Join the employee roster to the badge scans on the correct key "
                "and show who arrived late."
            ),
            "files": {
                "roster.xlsx": pd.DataFrame(
                    {"emp_code": ["E1", "E2"], "name": ["Kim", "Lee"]}
                ),
                "badge_scans.xlsx": pd.DataFrame(
                    {"badge_no": ["B9", "B8"], "scan_time": ["09:10", "08:55"]}
                ),
            },
            "expected_behavior": "correct_cannot_plan_or_refuse",
        }
    )

    # F. Anchors — 2
    cases.append(
        {
            "case_id": "P39I-11",
            "category": "F_anchor_p39e14_family",
            "prompt": (
                "Compare lane load for run R1 versus run R2 and keep both run "
                "loads visible by lane_id."
            ),
            "files": {
                "run_r1.xlsx": pd.DataFrame(
                    {"lane_id": ["L1", "L2"], "load": [100, 80]}
                ),
                "run_r2.xlsx": pd.DataFrame(
                    {"lane_id": ["L1", "L2"], "load": [110, 75]}
                ),
            },
            "anchor": "p39e14_valid_dual",
            "expected_verifier": "pass",
            "expected_manual_shadow": "YES",
        }
    )
    cases.append(
        {
            "case_id": "P39I-12",
            "category": "F_anchor_p39g11_family",
            "prompt": (
                "Compare chamber use for interval I1 versus interval I2 and keep "
                "both interval totals visible by chamber_id."
            ),
            "files": {
                "interval_i1.xlsx": pd.DataFrame(
                    {"chamber_id": ["H1", "H2"], "use_kwh": [8, 9]}
                ),
                "interval_i2.xlsx": pd.DataFrame(
                    {"chamber_id": ["H1", "H2"], "use_kwh": [7, 10]}
                ),
            },
            "anchor": "p39g11_fake_dual",
            "review_focus": "fake_dual_if_union_double_alias",
            "note": (
                "P39G-11-family pressure with new nouns. If planner emits "
                "union+identical aggregate aliases, verifier must NON-PASS."
            ),
        }
    )

    # Extra B-style to reach 13 (not fixture-heavy)
    cases.append(
        {
            "case_id": "P39I-13",
            "category": "B_agg_after_integration",
            "prompt": (
                "Merge item prices into the cart lines on item_id, then total "
                "line_value by cart_id where line_value is qty times price."
            ),
            "files": {
                "cart_lines.xlsx": pd.DataFrame(
                    {
                        "cart_id": ["G1", "G1", "G2"],
                        "item_id": ["I1", "I2", "I1"],
                        "qty": [2, 1, 3],
                    }
                ),
                "item_prices.xlsx": pd.DataFrame(
                    {"item_id": ["I1", "I2"], "price": [10.0, 4.0]}
                ),
            },
            "note": "May be PARTIAL if derived line_value not supported by DSL.",
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
    before_files: dict[str, int],
    *,
    timeout_s: float = 1800.0,
) -> list[dict[str, Any]]:
    """Wait for a new telemetry record or for inflight to drain.

    Do NOT reset the worker while a job is still running — that orphans LLM
    work and causes GPU/ollama contention on the next case.
    """
    t0 = time.time()
    last_log = 0.0
    while time.time() - t0 < timeout_s:
        got = _all_new_records(before_files)
        if got:
            time.sleep(0.8)
            more = _all_new_records(before_files)
            # Also wait briefly for reserved to drop after write.
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
    # Last chance collect; do not reset while reserved>0.
    got = _all_new_records(before_files)
    if got:
        return got
    # If still reserved, wait up to +10min for drain before next case.
    drain_deadline = time.time() + 600.0
    while get_inflight_for_tests() > 0 and time.time() < drain_deadline:
        print(
            f"  …draining orphan inflight={get_inflight_for_tests()}",
            flush=True,
        )
        time.sleep(10.0)
        got = _all_new_records(before_files)
        if got:
            return got
    return _all_new_records(before_files)


def run_pilot() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    TEL.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)

    # Confirm V2.2 before starting
    from core.integrate.semantic_verifier import run_semantic_verification
    import inspect

    mode = inspect.signature(run_semantic_verification).parameters[
        "materialization_mode"
    ].default
    if mode != "final_schema_expr_partition":
        raise RuntimeError(f"Refuse observation: materialization={mode!r}, need V2.2")

    tel_abs = str(TEL.resolve())
    TEL.mkdir(parents=True, exist_ok=True)
    os.environ["MULTI_SHADOW_ENABLED"] = "true"
    os.environ["MULTI_SHADOW_SAMPLE_RATE"] = "1.0"
    os.environ["MULTI_SHADOW_INLINE_FOR_TESTS"] = "false"
    os.environ["MULTI_SHADOW_TELEMETRY_DIR"] = tel_abs
    os.environ["MULTI_SHADOW_STORE_PROMPT"] = "true"
    os.environ["MULTI_SHADOW_MAX_CONCURRENCY"] = "1"
    os.environ["MULTI_SHADOW_QUEUE_SIZE"] = "8"
    os.environ["MULTI_SHADOW_TIMEOUT_SEC"] = "600"

    reset_shadow_worker_for_tests()
    cfg = reload_config_for_tests()
    print(
        "Shadow config:",
        f"enabled={cfg.enabled}",
        f"telemetry_dir={cfg.telemetry_dir}",
        f"inline={cfg.inline_for_tests}",
        f"timeout={cfg.timeout_sec}",
        f"materialization=final_schema_expr_partition",
        flush=True,
    )
    if not cfg.enabled:
        raise RuntimeError("Shadow failed to enable for session")
    if str(cfg.telemetry_dir) != tel_abs:
        raise RuntimeError(
            f"telemetry dir mismatch: {cfg.telemetry_dir!r} vs {tel_abs!r}"
        )

    cases = _cases()
    request_set = []
    for c in cases:
        request_set.append(
            {
                "case_id": c["case_id"],
                "category": c["category"],
                "prompt": c["prompt"],
                "n_files": len(c["files"]),
                "file_names": list(c["files"]),
                "anchor": c.get("anchor"),
                "review_focus": c.get("review_focus"),
                "expected_verifier": c.get("expected_verifier"),
                "expected_manual_shadow": c.get("expected_manual_shadow"),
                "note": c.get("note"),
            }
        )
    (OUT / "pilot_request_set.json").write_text(
        json.dumps(request_set, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    file_line_pos: dict[str, int] = {}
    rows: list[dict[str, Any]] = []

    for i, case in enumerate(cases, 1):
        cid = case["case_id"]
        print(f"\n=== [{i}/{len(cases)}] {cid} {case['category']} ===", flush=True)
        case_dir = DATA / cid
        case_dir.mkdir(parents=True, exist_ok=True)
        named: list[tuple[str, pd.DataFrame]] = []
        for name, df in case["files"].items():
            df.to_excel(case_dir / name, index=False)
            named.append((name, df.copy()))

        for p in TEL.glob("shadow_*.jsonl"):
            file_line_pos[str(p)] = len(p.read_text(encoding="utf-8").splitlines())

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

        row = {
            "request_id": f"req-{i:02d}",
            "case_id": cid,
            "phase": "39I",
            "candidate_version": "Phase39H-V2.2",
            "materialization_mode": "final_schema_expr_partition",
            "category": case["category"],
            "anchor": case.get("anchor"),
            "review_focus": case.get("review_focus"),
            "prompt": case["prompt"],
            "files": list(case["files"]),
            "legacy_status": (
                "exception"
                if legacy_err
                else (
                    "success"
                    if legacy_df is not None or (legacy_reply and len(str(legacy_reply)) > 0)
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
            "final_columns": shadow.get("final_columns")
            or shadow.get("result_columns"),
            "comparison": (shadow_rec or {}).get("comparison"),
            "shadow_error_family": shadow.get("error_family"),
            "shadow_error_message": shadow.get("error_message"),
            "model_calls": shadow.get("model_calls"),
            "raw_shadow_record": shadow_rec,
            # Manual review fields filled after observation
            "legacy_correct": None,
            "shadow_correct": None,
            "ls_structural": None,
            "manual_review": None,
            "silent_wrong": None,
            "verifier_false_fail": None,
            "escalation_32b_class": None,
            "notes": None,
        }
        rows.append(row)
        (OUT / "observation_log.json").write_text(
            json.dumps(
                {
                    "phase": "39I",
                    "candidate": "Phase39H-V2.2",
                    "materialization_mode": "final_schema_expr_partition",
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
            "fail32b",
            row["failure_32b"],
            "sem32b",
            row["semantic_32b"],
            "t_legacy",
            legacy_latency,
            "t_shadow",
            row["shadow_latency_s"],
            flush=True,
        )

    report = {
        "phase": "39I",
        "candidate": "Phase39H-V2.2",
        "materialization_mode": "final_schema_expr_partition",
        "n": len(rows),
        "shadow_recorded": sum(1 for r in rows if r["shadow_recorded"]),
        "rows": rows,
    }
    (OUT / "observation_log.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    os.environ["MULTI_SHADOW_ENABLED"] = "false"
    reload_config_for_tests()
    print("\nShadow returned to OFF", flush=True)
    return report


if __name__ == "__main__":
    run_pilot()
