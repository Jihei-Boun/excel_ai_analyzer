"""Phase 39J — V2.2 another limited Shadow observation (measure-only).

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
OUT = ROOT / "benchmark_results/multi/phase39j"
TEL = OUT / "telemetry"
DATA = OUT / "datasets"

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = "qwen2.5:7b"


def _cases() -> list[dict[str, Any]]:
    """Targeted 10-case set filling Phase 39I evidence gaps."""
    cases: list[dict[str, Any]] = []

    cases.append(
        {
            "case_id": "P39J-01",
            "category": "O_ordinary_integration",
            "prompt": (
                "Combine invoices with vendor names on vendor_id and list "
                "invoice_id, vendor_name, and amount."
            ),
            "files": {
                "invoices.xlsx": pd.DataFrame(
                    {
                        "invoice_id": [101, 102, 103],
                        "vendor_id": ["V1", "V2", "V1"],
                        "amount": [200, 150, 90],
                    }
                ),
                "vendors.xlsx": pd.DataFrame(
                    {
                        "vendor_id": ["V1", "V2"],
                        "vendor_name": ["Acme", "Beta"],
                    }
                ),
            },
        }
    )
    cases.append(
        {
            "case_id": "P39J-02",
            "category": "O_ordinary_agg_after_integration",
            "prompt": (
                "Join tickets to teams on team_id, then show total effort_hours "
                "by team_name."
            ),
            "files": {
                "tickets.xlsx": pd.DataFrame(
                    {
                        "team_id": ["T1", "T1", "T2"],
                        "effort_hours": [3, 2, 5],
                    }
                ),
                "teams.xlsx": pd.DataFrame(
                    {
                        "team_id": ["T1", "T2"],
                        "team_name": ["Alpha", "Bravo"],
                    }
                ),
            },
        }
    )
    cases.append(
        {
            "case_id": "P39J-03",
            "category": "B_p39e14_valid_dual",
            "prompt": (
                "Compare pier North and pier South package counts by bay_id and "
                "keep both pier values visible."
            ),
            "files": {
                "pier_north.xlsx": pd.DataFrame(
                    {"bay_id": ["B1", "B2"], "packages": [40, 35]}
                ),
                "pier_south.xlsx": pd.DataFrame(
                    {"bay_id": ["B1", "B2"], "packages": [42, 30]}
                ),
            },
            "anchor": "p39e14_valid_dual",
            "research_question": "RQ2",
            "review_focus": "p39e14_valid_dual_completion",
            "expected_verifier": "pass",
            "expected_manual_shadow": "YES",
            "note": (
                "P39E-14-family valid dual with P39I-05-like shape. "
                "New nouns vs P39I-11 MergeError case."
            ),
        }
    )
    cases.append(
        {
            "case_id": "P39J-04",
            "category": "A_same_origin_partitioned",
            "prompt": (
                "Using the ledger entries, compare week W1 versus week W2 "
                "totals by cost_center and keep both week totals visible. "
                "Use the cost center list to keep centers aligned."
            ),
            "files": {
                "ledger_entries.xlsx": pd.DataFrame(
                    {
                        "cost_center": ["C1", "C1", "C2", "C2", "C1", "C2"],
                        "week": ["W1", "W2", "W1", "W2", "W1", "W2"],
                        "amount": [10, 12, 20, 18, 5, 7],
                    }
                ),
                "cost_centers.xlsx": pd.DataFrame(
                    {
                        "cost_center": ["C1", "C2"],
                        "center_name": ["Ops", "Sales"],
                    }
                ),
            },
            "research_question": "RQ1",
            "review_focus": "same_origin_partitioned_dual",
            "note": "Same ledger; independent week partitions; comparison intent.",
        }
    )
    cases.append(
        {
            "case_id": "P39J-05",
            "category": "C_p39i09_rename_join",
            "prompt": (
                "Show lot1_price and lot2_price for each part_id after combining "
                "the two lot files."
            ),
            "files": {
                "lot1.xlsx": pd.DataFrame(
                    {"part_id": ["P1", "P2"], "price": [3.0, 4.0]}
                ),
                "lot2.xlsx": pd.DataFrame(
                    {"part_id": ["P1", "P2"], "price": [3.5, 3.8]}
                ),
            },
            "research_question": "RQ3",
            "review_focus": "p39i09_ff_recurrence",
            "note": "Rename+join dual structure analogous to P39I-09.",
        }
    )
    cases.append(
        {
            "case_id": "P39J-06",
            "category": "C_p39i09_rename_join",
            "prompt": (
                "Show depot_a_stock and depot_b_stock for each sku after "
                "combining the two depot inventory files."
            ),
            "files": {
                "depot_a.xlsx": pd.DataFrame(
                    {"sku": ["S1", "S2"], "stock": [12, 8]}
                ),
                "depot_b.xlsx": pd.DataFrame(
                    {"sku": ["S1", "S2"], "stock": [10, 9]}
                ),
            },
            "research_question": "RQ3",
            "review_focus": "p39i09_ff_recurrence",
            "note": "Second independent rename+join probe.",
        }
    )
    cases.append(
        {
            "case_id": "P39J-07",
            "category": "D_p39g11_fake_dual",
            "prompt": (
                "Compare zone use for slot S1 versus slot S2 and keep both "
                "slot totals visible by zone_id."
            ),
            "files": {
                "slot_s1.xlsx": pd.DataFrame(
                    {"zone_id": ["Z1", "Z2"], "use_kwh": [8, 9]}
                ),
                "slot_s2.xlsx": pd.DataFrame(
                    {"zone_id": ["Z1", "Z2"], "use_kwh": [7, 10]}
                ),
            },
            "anchor": "p39g11_fake_dual",
            "research_question": "RQ4",
            "review_focus": "fake_dual_if_union_double_alias",
            "note": (
                "P39G-11-family pressure. If union+identical aggregate aliases, "
                "must NON-PASS."
            ),
        }
    )
    cases.append(
        {
            "case_id": "P39J-08",
            "category": "A_same_origin_partitioned",
            "prompt": (
                "From the event log, compare open versus closed event counts "
                "by location_id and keep both status counts visible. Use the "
                "location list for alignment."
            ),
            "files": {
                "event_log.xlsx": pd.DataFrame(
                    {
                        "location_id": ["L1", "L1", "L2", "L2", "L1", "L2"],
                        "status": [
                            "open",
                            "closed",
                            "open",
                            "closed",
                            "open",
                            "closed",
                        ],
                        "events": [1, 1, 1, 1, 1, 1],
                    }
                ),
                "locations.xlsx": pd.DataFrame(
                    {
                        "location_id": ["L1", "L2"],
                        "location_name": ["Dock", "Yard"],
                    }
                ),
            },
            "research_question": "RQ1",
            "review_focus": "same_origin_partitioned_dual",
            "note": "Same-origin status partition backup cell.",
        }
    )
    cases.append(
        {
            "case_id": "P39J-09",
            "category": "O_ordinary_union",
            "prompt": "Stack the two compatible delivery logs into one detail table.",
            "files": {
                "deliveries_am.xlsx": pd.DataFrame(
                    {"delivery_id": ["D1", "D2"], "kg": [10.0, 12.5]}
                ),
                "deliveries_pm.xlsx": pd.DataFrame(
                    {"delivery_id": ["D3", "D4"], "kg": [9.0, 11.0]}
                ),
            },
        }
    )
    cases.append(
        {
            "case_id": "P39J-10",
            "category": "O_ambiguous_cannot_plan",
            "prompt": (
                "Join the staff directory to the access events on the correct "
                "key and show who entered after hours."
            ),
            "files": {
                "staff_directory.xlsx": pd.DataFrame(
                    {"staff_code": ["X1", "X2"], "name": ["Park", "Choi"]}
                ),
                "access_events.xlsx": pd.DataFrame(
                    {"card_id": ["C9", "C8"], "event_time": ["22:10", "08:55"]}
                ),
            },
            "expected_behavior": "correct_cannot_plan_or_refuse",
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
    got = _all_new_records(before_files)
    if got:
        return got
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

    from core.integrate.semantic_verifier import run_semantic_verification
    import inspect

    mode = inspect.signature(run_semantic_verification).parameters[
        "materialization_mode"
    ].default
    if mode != "final_schema_expr_partition":
        raise RuntimeError(
            f"Refuse observation: materialization={mode!r}, need V2.2"
        )

    tel_abs = str(TEL.resolve())
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
        f"concurrency={cfg.max_concurrency}",
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
                "research_question": c.get("research_question"),
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
            file_line_pos[str(p)] = len(
                p.read_text(encoding="utf-8").splitlines()
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

        row = {
            "request_id": f"req-{i:02d}",
            "case_id": cid,
            "phase": "39J",
            "candidate_version": "Phase39H-V2.2",
            "materialization_mode": "final_schema_expr_partition",
            "category": case["category"],
            "anchor": case.get("anchor"),
            "research_question": case.get("research_question"),
            "review_focus": case.get("review_focus"),
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
            "plan_validation_status": shadow.get("plan_validation_status"),
            "plan_validation_codes": shadow.get("plan_validation_codes"),
            "raw_shadow_record": shadow_rec,
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
                    "phase": "39J",
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
            "path",
            row["final_path"],
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
        "phase": "39J",
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
    cfg_off = reload_config_for_tests()
    proof = {
        "MULTI_SHADOW_ENABLED_env": os.environ.get("MULTI_SHADOW_ENABLED"),
        "config_enabled": bool(cfg_off.enabled),
        "confirmed_off": not bool(cfg_off.enabled),
    }
    (OUT / "shadow_off_proof.json").write_text(
        json.dumps(proof, indent=2), encoding="utf-8"
    )
    print("\nShadow returned to OFF", proof, flush=True)
    return report


if __name__ == "__main__":
    run_pilot()
