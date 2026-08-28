"""Phase 39G — V2 limited Shadow re-observation (measure-only).

Frozen candidate: Independent Verifier + final_schema_origins (V2).
Legacy remains user-visible. Shadow session-only, fire-and-forget.
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
OUT = ROOT / "benchmark_results/multi/phase39g"
TEL = OUT / "telemetry"
DATA = OUT / "datasets"

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = "qwen2.5:7b"


def _cases() -> list[dict[str, Any]]:
    """12 production-like requests; mostly new vs Phase 39E."""
    cases: list[dict[str, Any]] = []

    # A. Valid straightforward integration — 2
    cases.append(
        {
            "record_id": "P39G-01",
            "group": "A_valid_integration",
            "prompt": "Join tickets to agents on agent_id and list ticket_id, agent_name, priority.",
            "files": {
                "tickets.xlsx": pd.DataFrame(
                    {
                        "ticket_id": [101, 102, 103],
                        "agent_id": ["A1", "A2", "A1"],
                        "priority": ["high", "low", "med"],
                    }
                ),
                "agents.xlsx": pd.DataFrame(
                    {
                        "agent_id": ["A1", "A2"],
                        "agent_name": ["Nora", "Omar"],
                    }
                ),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39G-02",
            "group": "A_valid_integration",
            "prompt": "Stack the two same-schema sensor logs into one detail table.",
            "files": {
                "sensor_run1.xlsx": pd.DataFrame(
                    {"sensor_id": ["X1", "X2"], "reading": [1.1, 2.2]}
                ),
                "sensor_run2.xlsx": pd.DataFrame(
                    {"sensor_id": ["X3", "X4"], "reading": [3.3, 4.4]}
                ),
            },
        }
    )

    # B. Valid dual-side comparison — 3
    cases.append(
        {
            "record_id": "P39G-03",
            "group": "B_dual_side",
            "prompt": "Compare morning and evening throughput by line_id and keep both values visible.",
            "files": {
                "morning.xlsx": pd.DataFrame(
                    {"line_id": ["L1", "L2"], "throughput": [50, 40]}
                ),
                "evening.xlsx": pd.DataFrame(
                    {"line_id": ["L1", "L2"], "throughput": [55, 35]}
                ),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39G-04",
            "group": "B_dual_side",
            "prompt": "Show before and after defect_rate side by side for each station_id.",
            "files": {
                "before.xlsx": pd.DataFrame(
                    {"station_id": ["ST1", "ST2"], "defect_rate": [0.02, 0.05]}
                ),
                "after.xlsx": pd.DataFrame(
                    {"station_id": ["ST1", "ST2"], "defect_rate": [0.01, 0.04]}
                ),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39G-05",
            "group": "B_dual_side_rec12_like",
            "prompt": (
                "Compare week1 and week2 units by store_id, keep both weeks visible, "
                "and identify which stores increased."
            ),
            "files": {
                "week1_units.xlsx": pd.DataFrame(
                    {"store_id": ["ST1", "ST2", "ST3"], "units": [100, 200, 150]}
                ),
                "week2_units.xlsx": pd.DataFrame(
                    {"store_id": ["ST1", "ST2", "ST3"], "units": [120, 180, 160]}
                ),
            },
            "full_intent_check": True,
        }
    )

    # C. Three-file / multi-hop — 2
    cases.append(
        {
            "record_id": "P39G-06",
            "group": "C_multihop",
            "prompt": "Using courses, enrollments, and students, list course_title with student_name.",
            "files": {
                "courses.xlsx": pd.DataFrame(
                    {"course_id": ["C1", "C2"], "course_title": ["Math", "History"]}
                ),
                "enrollments.xlsx": pd.DataFrame(
                    {"course_id": ["C1", "C2"], "student_id": ["S1", "S2"]}
                ),
                "students.xlsx": pd.DataFrame(
                    {"student_id": ["S1", "S2"], "student_name": ["Ann", "Ben"]}
                ),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39G-07",
            "group": "C_multihop",
            "prompt": "Join vendors to purchase_orders to items and show vendor_name, item_name, qty.",
            "files": {
                "vendors.xlsx": pd.DataFrame(
                    {"vendor_id": ["V1"], "vendor_name": ["Acme"]}
                ),
                "purchase_orders.xlsx": pd.DataFrame(
                    {"po_id": ["PO1"], "vendor_id": ["V1"], "item_id": ["I1"], "qty": [3]}
                ),
                "items.xlsx": pd.DataFrame(
                    {"item_id": ["I1"], "item_name": ["Bolt"]}
                ),
            },
        }
    )

    # D. Legitimate combine/aggregate — 2
    cases.append(
        {
            "record_id": "P39G-08",
            "group": "D_non_comparison_agg",
            "prompt": "Combine both donation files and calculate the overall amount total.",
            "files": {
                "donations_q1.xlsx": pd.DataFrame({"amount": [10, 20]}),
                "donations_q2.xlsx": pd.DataFrame({"amount": [30]}),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39G-09",
            "group": "D_non_comparison_agg",
            "prompt": "Append both inventory snapshots then summarize total units by sku across all rows.",
            "files": {
                "inv_a.xlsx": pd.DataFrame({"sku": ["K1", "K2"], "units": [5, 6]}),
                "inv_b.xlsx": pd.DataFrame({"sku": ["K1", "K3"], "units": [7, 8]}),
            },
        }
    )

    # E. Ambiguous/impossible — 1
    cases.append(
        {
            "record_id": "P39G-10",
            "group": "E_ambiguous",
            "prompt": "Join these unrelated tables on a shared key and compare their main metrics.",
            "files": {
                "playlist.xlsx": pd.DataFrame({"track": ["t1"], "bpm": [120]}),
                "payroll.xlsx": pd.DataFrame({"emp_id": ["E9"], "salary": [5000]}),
            },
            "expect_cannot_plan_ok": True,
        }
    )

    # F. Diagnostic anchors — 2
    # Anchor 1: P39E-14 family (rename + join dual-side) — new file names/wording
    cases.append(
        {
            "record_id": "P39G-11",
            "group": "ANCHOR_1_p39e14_family",
            "prompt": (
                "Compare node electricity use for window W1 versus window W2 "
                "and keep both window totals visible by node_id."
            ),
            "files": {
                "w1_usage.xlsx": pd.DataFrame(
                    {"node_id": ["N1", "N2"], "kwh": [10, 20]}
                ),
                "w2_usage.xlsx": pd.DataFrame(
                    {"node_id": ["N1", "N2"], "kwh": [12, 18]}
                ),
            },
            "anchor": "1",
            "expected_verifier": "pass",
        }
    )
    # Anchor 2: same-origin fake dual-side temptation (single source)
    # One file only; ask for two side columns — if planner fabricates dual labels
    # from the same metric, V2 origins should yield non-pass.
    cases.append(
        {
            "record_id": "P39G-12",
            "group": "ANCHOR_2_same_origin_fake_dual",
            "prompt": (
                "Using only cohort_scores.xlsx, show score_baseline and score_treatment "
                "side by side for each entity_id."
            ),
            "files": {
                "cohort_scores.xlsx": pd.DataFrame(
                    {"entity_id": ["E1", "E2"], "score": [0.4, 0.6]}
                ),
            },
            "anchor": "2",
            "expected_verifier_not": "pass",
            "note": "same-origin / insufficient evidence family",
        }
    )

    return cases


def _wait_shadow_idle(timeout_s: float = 1500.0) -> None:
    """Wait until Shadow inflight drains.

    Pipeline soft-timeout is 600s but long 32B runs can exceed that and still
    finish later; collect after drain (or after this wait budget).
    """
    t0 = time.time()
    saw_inflight = False
    while time.time() - t0 < timeout_s:
        n = get_inflight_for_tests()
        if n > 0:
            saw_inflight = True
        if n <= 0:
            # Brief grace for telemetry append after inflight decrement.
            time.sleep(1.2)
            if get_inflight_for_tests() <= 0:
                if saw_inflight or (time.time() - t0) > 2.0:
                    return
        time.sleep(1.0)
    raise TimeoutError("shadow worker did not drain")


def _all_new_records(before_files: dict[str, int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted(TEL.glob("shadow_*.jsonl")):
        prev = before_files.get(str(p), 0)
        lines = p.read_text(encoding="utf-8").splitlines()
        for line in lines[prev:]:
            if line.strip():
                out.append(json.loads(line))
        before_files[str(p)] = len(lines)
    return out


def _collect_shadow_after_wait(
    before_files: dict[str, int], *, extra_poll_s: float = 15.0
) -> list[dict[str, Any]]:
    """Collect new telemetry; poll briefly for late appends after soft-timeout."""
    got = _all_new_records(before_files)
    if got:
        return got
    t0 = time.time()
    while time.time() - t0 < extra_poll_s:
        time.sleep(1.0)
        got = _all_new_records(before_files)
        if got:
            return got
    return got


def run_pilot() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    TEL.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)

    os.environ["MULTI_SHADOW_ENABLED"] = "true"
    os.environ["MULTI_SHADOW_SAMPLE_RATE"] = "1.0"
    os.environ["MULTI_SHADOW_INLINE_FOR_TESTS"] = "false"
    os.environ["MULTI_SHADOW_TELEMETRY_DIR"] = str(TEL)
    os.environ["MULTI_SHADOW_STORE_PROMPT"] = "true"
    os.environ["MULTI_SHADOW_MAX_CONCURRENCY"] = "1"
    os.environ["MULTI_SHADOW_QUEUE_SIZE"] = "8"

    reset_shadow_worker_for_tests()
    reload_config_for_tests()

    cases = _cases()
    (OUT / "pilot_request_set.json").write_text(
        json.dumps(
            [
                {
                    "record_id": c["record_id"],
                    "group": c["group"],
                    "prompt": c["prompt"],
                    "n_files": len(c["files"]),
                    "file_names": list(c["files"]),
                    "anchor": c.get("anchor"),
                }
                for c in cases
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    file_line_pos: dict[str, int] = {}
    rows: list[dict[str, Any]] = []

    for i, case in enumerate(cases, 1):
        rid = case["record_id"]
        print(f"\n=== [{i}/{len(cases)}] {rid} {case['group']} ===", flush=True)
        case_dir = DATA / rid
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

        try:
            _wait_shadow_idle(timeout_s=1500.0)
        except TimeoutError as exc:
            print("SHADOW WAIT TIMEOUT", exc, flush=True)

        new_recs = _collect_shadow_after_wait(file_line_pos, extra_poll_s=20.0)
        shadow_rec = new_recs[-1] if new_recs else None
        shadow = (shadow_rec or {}).get("shadow") or {}
        legacy_tel = (shadow_rec or {}).get("legacy") or {}

        row = {
            "record_id": rid,
            "phase": "39G",
            "candidate_version": "Phase39F-V2",
            "group": case["group"],
            "anchor": case.get("anchor"),
            "prompt": case["prompt"],
            "files": list(case["files"]),
            "legacy_status": (
                "exception"
                if legacy_err
                else (
                    "success"
                    if legacy_df is not None or (legacy_reply and len(legacy_reply) > 0)
                    else "unknown"
                )
            ),
            "legacy_operation": legacy_op,
            "legacy_reply_preview": (legacy_reply or "")[:500],
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
            "comparison": (shadow_rec or {}).get("comparison"),
            "shadow_error_family": shadow.get("error_family"),
            "shadow_error_message": shadow.get("error_message"),
            "model_calls": shadow.get("model_calls"),
            "raw_shadow_record": shadow_rec,
            "legacy_correct": None,
            "shadow_correct": None,
            "manual_review": None,
            "silent_wrong": None,
            "verifier_false_fail": None,
            "notes_ko": None,
        }
        rows.append(row)
        (OUT / "observation_log_partial.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, default=str),
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
        "phase": "39G",
        "candidate": "Phase39F-V2",
        "n": len(rows),
        "shadow_recorded": sum(1 for r in rows if r["shadow_recorded"]),
        "rows": rows,
    }
    (OUT / "observation_log_raw.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.environ["MULTI_SHADOW_ENABLED"] = "false"
    reload_config_for_tests()
    print("\nShadow returned to OFF", flush=True)
    return report


if __name__ == "__main__":
    run_pilot()
