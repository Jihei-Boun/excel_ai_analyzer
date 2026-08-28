"""Phase 39E — limited controlled Shadow observation (measure-only).

Legacy remains user-visible via route_multi_prompt.
Shadow is enabled for this session only (env overrides), fire-and-forget,
then we wait for worker drain to collect telemetry for review.
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
OUT = ROOT / "benchmark_results/multi/phase39e"
TEL = OUT / "telemetry"
DATA = OUT / "datasets"

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = "qwen2.5:7b"


def _cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    cases.append(
        {
            "record_id": "P39E-01",
            "group": "A_valid_integration",
            "prompt": "Join orders and customers on customer_id and list order_id, customer_name, amount.",
            "files": {
                "orders.xlsx": pd.DataFrame(
                    {
                        "order_id": [1, 2, 3],
                        "customer_id": ["C1", "C2", "C1"],
                        "amount": [100, 200, 150],
                    }
                ),
                "customers.xlsx": pd.DataFrame(
                    {
                        "customer_id": ["C1", "C2"],
                        "customer_name": ["Ada", "Bob"],
                    }
                ),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39E-02",
            "group": "A_valid_integration",
            "prompt": "Union the two same-schema event logs into one detail table.",
            "files": {
                "events_a.xlsx": pd.DataFrame(
                    {"event_id": [1, 2], "user": ["u1", "u2"], "score": [1, 2]}
                ),
                "events_b.xlsx": pd.DataFrame(
                    {"event_id": [3, 4], "user": ["u3", "u4"], "score": [3, 4]}
                ),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39E-03",
            "group": "A_valid_integration",
            "prompt": "Combine both shipment files and show total qty by warehouse_id.",
            "files": {
                "ship_east.xlsx": pd.DataFrame(
                    {"warehouse_id": ["W1", "W2"], "qty": [10, 20]}
                ),
                "ship_west.xlsx": pd.DataFrame(
                    {"warehouse_id": ["W1", "W3"], "qty": [5, 7]}
                ),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39E-04",
            "group": "B_distinction",
            "prompt": "Compare Q1 and Q2 revenue by region and keep both quarter totals visible.",
            "files": {
                "q1_revenue.xlsx": pd.DataFrame(
                    {"region": ["North", "South"], "revenue": [100, 80]}
                ),
                "q2_revenue.xlsx": pd.DataFrame(
                    {"region": ["North", "South"], "revenue": [120, 70]}
                ),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39E-05",
            "group": "B_distinction",
            "prompt": "Show planned vs actual hours side by side for each project_id.",
            "files": {
                "planned_hours.xlsx": pd.DataFrame(
                    {"project_id": ["P1", "P2"], "hours": [40, 30]}
                ),
                "actual_hours.xlsx": pd.DataFrame(
                    {"project_id": ["P1", "P2"], "hours": [42, 28]}
                ),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39E-06",
            "group": "B_distinction",
            "prompt": "Contrast system_alpha latency with system_beta latency per endpoint; keep both sides.",
            "files": {
                "system_alpha.xlsx": pd.DataFrame(
                    {"endpoint": ["/a", "/b"], "latency_ms": [12, 20]}
                ),
                "system_beta.xlsx": pd.DataFrame(
                    {"endpoint": ["/a", "/b"], "latency_ms": [10, 25]}
                ),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39E-07",
            "group": "C_multihop",
            "prompt": "Using projects, assignments, and employees, list project_name with employee_name.",
            "files": {
                "projects.xlsx": pd.DataFrame(
                    {"project_id": ["P1", "P2"], "project_name": ["Alpha", "Beta"]}
                ),
                "assignments.xlsx": pd.DataFrame(
                    {"project_id": ["P1", "P2"], "employee_id": ["E1", "E2"]}
                ),
                "employees.xlsx": pd.DataFrame(
                    {"employee_id": ["E1", "E2"], "employee_name": ["Kim", "Lee"]}
                ),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39E-08",
            "group": "C_multihop",
            "prompt": "Join stores to inventories to products and show store_name, product_name, stock.",
            "files": {
                "stores.xlsx": pd.DataFrame(
                    {"store_id": ["S1"], "store_name": ["Central"]}
                ),
                "inventories.xlsx": pd.DataFrame(
                    {"store_id": ["S1"], "product_id": ["X1"], "stock": [9]}
                ),
                "products.xlsx": pd.DataFrame(
                    {"product_id": ["X1"], "product_name": ["Widget"]}
                ),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39E-09",
            "group": "D_non_comparison_agg",
            "prompt": "Combine both ledger files and calculate the overall amount total.",
            "files": {
                "ledger_a.xlsx": pd.DataFrame({"amount": [10, 20]}),
                "ledger_b.xlsx": pd.DataFrame({"amount": [30]}),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39E-10",
            "group": "D_non_comparison_agg",
            "prompt": "Append the detail rows then summarize total value by entity_id across all sources.",
            "files": {
                "part1.xlsx": pd.DataFrame(
                    {"entity_id": ["E1", "E2"], "value": [5, 6]}
                ),
                "part2.xlsx": pd.DataFrame(
                    {"entity_id": ["E1", "E3"], "value": [7, 8]}
                ),
            },
        }
    )
    cases.append(
        {
            "record_id": "P39E-11",
            "group": "E_ambiguous",
            "prompt": "Join these files and compare their primary metrics.",
            "files": {
                "weather.xlsx": pd.DataFrame({"city": ["Seoul"], "temp_c": [22]}),
                "invoices.xlsx": pd.DataFrame({"invoice_id": [1], "amount": [500]}),
            },
            "expect_cannot_plan_ok": True,
        }
    )
    cases.append(
        {
            "record_id": "P39E-12",
            "group": "E_ambiguous",
            "prompt": "Merge the datasets on a shared key and rank the top performers.",
            "files": {
                "colors.xlsx": pd.DataFrame({"hex": ["#fff"], "name": ["white"]}),
                "primes.xlsx": pd.DataFrame({"n": [2, 3, 5]}),
            },
            "expect_cannot_plan_ok": True,
        }
    )
    cases.append(
        {
            "record_id": "P39E-13",
            "group": "ANCHOR_A_finance_aspirational_risk",
            "prompt": "Show actual spend and budgeted spend side by side for each cost_center.",
            "files": {
                "actuals.xlsx": pd.DataFrame(
                    {"cost_center": ["CC1", "CC2"], "amount": [100, 200]}
                ),
                "budget.xlsx": pd.DataFrame(
                    {"cost_center": ["CC1", "CC2"], "amount": [90, 210]}
                ),
            },
            "anchor": "A",
        }
    )
    cases.append(
        {
            "record_id": "P39E-14",
            "group": "ANCHOR_B_energy_dual_side",
            "prompt": (
                "Compare site electricity use for period P1 versus period P2 "
                "and keep both period totals visible by site_id."
            ),
            "files": {
                "p1_usage.xlsx": pd.DataFrame(
                    {"site_id": ["S1", "S2"], "kwh": [10, 20]}
                ),
                "p2_usage.xlsx": pd.DataFrame(
                    {"site_id": ["S1", "S2"], "kwh": [12, 18]}
                ),
            },
            "anchor": "B",
        }
    )
    cases.append(
        {
            "record_id": "P39E-15",
            "group": "ANCHOR_C_rec12_like",
            "prompt": "Compare July and August regional sales and tell me which regions increased.",
            "files": {
                "july_sales.xlsx": pd.DataFrame(
                    {"region": ["A", "B", "C"], "sales": [100, 200, 150]}
                ),
                "august_sales.xlsx": pd.DataFrame(
                    {"region": ["A", "B", "C"], "sales": [120, 180, 160]}
                ),
            },
            "anchor": "C",
        }
    )
    return cases


def _wait_shadow_idle(timeout_s: float = 900.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if get_inflight_for_tests() <= 0:
            time.sleep(0.8)
            if get_inflight_for_tests() <= 0:
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

        try:
            _wait_shadow_idle(timeout_s=900.0)
        except TimeoutError as exc:
            print("SHADOW WAIT TIMEOUT", exc, flush=True)

        new_recs = _all_new_records(file_line_pos)
        shadow_rec = new_recs[-1] if new_recs else None
        shadow = (shadow_rec or {}).get("shadow") or {}
        legacy_tel = (shadow_rec or {}).get("legacy") or {}

        row = {
            "record_id": rid,
            "phase": "39E",
            "candidate_version": "Phase39D-V1",
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
            "shadow_latency_s": shadow.get("latency_total_s"),
            "final_plan": shadow.get("final_plan"),
            "result_fingerprint": shadow.get("result_fingerprint"),
            "comparison": (shadow_rec or {}).get("comparison"),
            "shadow_error_family": shadow.get("error_family"),
            "shadow_error_message": shadow.get("error_message"),
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
            "t_legacy",
            legacy_latency,
            "t_shadow",
            row["shadow_latency_s"],
            flush=True,
        )

    report = {
        "phase": "39E",
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
    return report


if __name__ == "__main__":
    run_pilot()
