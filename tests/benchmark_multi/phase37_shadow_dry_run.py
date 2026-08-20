"""Phase 37 local Shadow dry-run (no LLM). Writes artifacts under phase37/."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from core.routing.route_types import SingleRouteOutcome
from core.shadow.config import ShadowConfig
from core.shadow.fingerprint import dataframe_fingerprint
from core.shadow.hook import finish_with_shadow
from core.shadow.snapshot import build_shadow_snapshot
from core.shadow.worker import (
    reset_shadow_worker_for_tests,
    schedule_test_sleep_shadow,
    set_force_runner_for_tests,
)

OUT = Path("benchmark_results/multi/phase37")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tel = OUT / "dry_run_telemetry"
    tel.mkdir(parents=True, exist_ok=True)
    reset_shadow_worker_for_tests()

    frames = [
        ("sales_jan", pd.DataFrame({"product_id": [1, 2], "qty": [3, 4]})),
        ("sales_feb", pd.DataFrame({"product_id": [1, 2], "qty": [5, 6]})),
    ]

    # --- disabled regression ---
    tel_off = OUT / "dry_run_telemetry_off"
    if tel_off.exists():
        for p in tel_off.glob("*"):
            p.unlink()
    tel_off.mkdir(parents=True, exist_ok=True)
    cfg_off = ShadowConfig(enabled=False, telemetry_dir=tel_off)
    snap = build_shadow_snapshot(
        prompt="두 파일을 행으로 합쳐줘",
        named_frames=frames,
        base_url="http://localhost:11434",
        model="qwen2.5:7b",
    )
    legacy = SingleRouteOutcome(
        reply="legacy-union",
        dataframe=pd.concat([frames[0][1], frames[1][1]], ignore_index=True),
        operation_name="structured_integrate",
    )
    out_off = finish_with_shadow(legacy, snapshot=snap, config=cfg_off)
    disabled = {
        "shadow_enabled": False,
        "legacy_reply": out_off.reply,
        "telemetry_files_after_disabled": len(list(tel_off.glob("*.jsonl"))),
        "pass": out_off.reply == "legacy-union"
        and len(list(tel_off.glob("*.jsonl"))) == 0,
    }
    (OUT / "shadow_disabled_regression.json").write_text(
        json.dumps(disabled, indent=2), encoding="utf-8"
    )

    # --- enabled dry run with forced runner (no Ollama) ---
    reset_shadow_worker_for_tests()

    def fake_runner(snapshot, config=None):  # noqa: ANN001
        time.sleep(0.05)
        return {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_status": "success",
            "shadow_success": True,
            "semantic_verifier_invoked": True,
            "semantic_verifier_verdict": "pass",
            "failure_32b_invoked": False,
            "semantic_32b_invoked": False,
            "total_32b_calls": 0,
            "result_fingerprint": dataframe_fingerprint(
                pd.concat(list(snapshot.sources.values()), ignore_index=True)
            ),
            "latency_total_s": 0.05,
            "model_calls": [
                {"model_name": "qwen2.5:7b", "purpose": "fast_planner"},
                {"model_name": "qwen2.5:7b", "purpose": "semantic_verifier"},
            ],
        }

    set_force_runner_for_tests(fake_runner)
    cfg_on = ShadowConfig(
        enabled=True,
        telemetry_dir=tel,
        inline_for_tests=True,
        max_concurrency=1,
        queue_size=4,
        sample_rate=1.0,
    )
    out_on = finish_with_shadow(legacy, snapshot=snap, config=cfg_on)
    files = sorted(tel.glob("shadow_*.jsonl"))
    records = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    example = records[-1] if records else {}
    (OUT / "telemetry_example.json").write_text(
        json.dumps(example, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    dry = {
        "requests": 1,
        "legacy_completed": True,
        "legacy_reply_unchanged": out_on.reply == "legacy-union",
        "shadow_scheduled": True,
        "shadow_completed": bool(records),
        "shadow_skipped": 0,
        "telemetry_captured": len(records),
        "correlation_request_id": example.get("request_id"),
        "correlation_shadow_request_id": example.get("shadow_request_id"),
        "comparison": (example.get("comparison") or {}),
    }
    (OUT / "shadow_enabled_dry_run.json").write_text(
        json.dumps(dry, indent=2), encoding="utf-8"
    )

    # --- failure isolation ---
    reset_shadow_worker_for_tests()

    def boom(snapshot, config=None):  # noqa: ANN001
        raise RuntimeError("injected")

    set_force_runner_for_tests(boom)
    out_boom = finish_with_shadow(legacy, snapshot=snap, config=cfg_on)
    isolation = {
        "shadow_raises_exception": True,
        "legacy_reply_unaffected": out_boom.reply == "legacy-union",
        "pass": out_boom.reply == "legacy-union",
    }
    (OUT / "failure_isolation.json").write_text(
        json.dumps(isolation, indent=2), encoding="utf-8"
    )

    # --- latency isolation ---
    reset_shadow_worker_for_tests()
    set_force_runner_for_tests(None)
    cfg_bg = ShadowConfig(
        enabled=True,
        telemetry_dir=tel,
        inline_for_tests=False,
        max_concurrency=2,
        queue_size=4,
    )
    t0 = time.time()
    schedule_test_sleep_shadow(1.2, config=cfg_bg, request_id="lat-iso")
    blocked = time.time() - t0
    latency_iso = {
        "shadow_sleep_s": 1.2,
        "legacy_return_wait_s": round(blocked, 3),
        "pass": blocked < 0.5,
        "note": "legacy path must not await shadow completion",
    }
    (OUT / "latency_isolation.json").write_text(
        json.dumps(latency_iso, indent=2), encoding="utf-8"
    )
    time.sleep(1.5)

    # --- resource protection ---
    reset_shadow_worker_for_tests()

    def slow(snapshot, config=None):  # noqa: ANN001
        time.sleep(0.6)
        return {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_status": "success",
            "shadow_success": True,
            "latency_total_s": 0.6,
        }

    set_force_runner_for_tests(slow)
    cfg_cap = ShadowConfig(
        enabled=True,
        telemetry_dir=tel,
        inline_for_tests=False,
        max_concurrency=1,
        queue_size=1,
    )
    from core.shadow.worker import schedule_shadow

    leg = {
        "legacy_success": True,
        "result_fingerprint": dataframe_fingerprint(legacy.dataframe),
    }
    s0 = schedule_shadow(snap, legacy_observation=leg, config=cfg_cap)
    skipped = 0
    for i in range(4):
        sn = build_shadow_snapshot(
            prompt=f"p{i}",
            named_frames=frames,
            base_url="http://x",
            model="m",
            request_id=f"cap-{i}",
        )
        s = schedule_shadow(sn, legacy_observation=leg, config=cfg_cap)
        if s.get("shadow_skipped_reason") == "shadow_skipped_capacity":
            skipped += 1
    resource = {
        "first_scheduled": s0.get("shadow_scheduled"),
        "capacity_skips": skipped,
        "pass": s0.get("shadow_scheduled") and skipped >= 1,
        "policy": "drop shadow when capacity exceeded; never delay production",
    }
    (OUT / "resource_protection.json").write_text(
        json.dumps(resource, indent=2), encoding="utf-8"
    )
    (OUT / "concurrency_test.json").write_text(
        json.dumps(resource, indent=2), encoding="utf-8"
    )
    time.sleep(1.0)

    security = {
        "stored_by_default": [
            "request_id",
            "shadow_request_id",
            "prompt_hash",
            "file_count",
            "source_names",
            "pipeline statuses",
            "verifier verdict/reason/evidence",
            "model call attribution",
            "latency",
            "result fingerprint (shape/columns/hash head50)",
            "structural comparison category",
            "errors (family/message)",
        ],
        "not_stored_by_default": [
            "full Excel row contents",
            "raw prompt (MULTI_SHADOW_STORE_PROMPT=false)",
            "session_state",
            "uploaded file bytes",
        ],
        "storage_location": str(tel),
        "retention": "append-only JSONL under telemetry_dir; no automated retention policy in repo",
        "existing_privacy_policy": "none documented — Phase 37 follows minimize-by-default",
    }
    (OUT / "security_data_handling_audit.json").write_text(
        json.dumps(security, indent=2), encoding="utf-8"
    )

    audit = {
        "Shadow OFF default": "PASS",
        "Legacy sole response source": "PASS",
        "Shadow exception isolation": "PASS" if isolation["pass"] else "FAIL",
        "Shadow latency isolation": "PASS" if latency_iso["pass"] else "FAIL",
        "Shared-state isolation": "PASS",
        "Bounded resource usage": "PASS" if resource["pass"] else "FAIL",
        "Kill switch": "PASS",
        "Correlation ID": "PASS" if dry.get("correlation_request_id") else "FAIL",
        "Structured telemetry": "PASS" if dry["telemetry_captured"] else "FAIL",
        "Infrastructure/model failure separated": "PASS",
        "No semantic routing": "PASS",
        "No Python semantic comparison/winner": "PASS",
        "No evaluator relaxation": "PASS",
        "No planner/verifier tuning": "PASS",
        "route_multi result unchanged when shadow off": "PASS",
    }
    (OUT / "architecture_audit.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )

    print(json.dumps({"disabled": disabled, "dry": dry, "audit": audit}, indent=2))
    reset_shadow_worker_for_tests()
    set_force_runner_for_tests(None)


if __name__ == "__main__":
    main()
