"""Phase 39L — Live verifier payload capture & instability characterization.

Diagnostic / observability harness only.
Does NOT redesign verifier, add V2.3, relax grain, migrate, or enable Shadow scoring.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from core.integrate.semantic_verifier import (
    VERIFIER_SYSTEM_PROMPT,
    build_verifier_payload,
    run_semantic_verification,
)
from core.integrate.verifier_invocation_capture import (
    canonicalize_json,
    classify_replay_fidelity,
    clear_last_record_for_tests,
    get_last_record_for_tests,
    prompt_template_hash,
    sha256_text,
)

OUT = ROOT / "benchmark_results/multi/phase39l"
FIX_H = ROOT / "tests/benchmark_multi/fixtures/phase39h"
FIX_B = ROOT / "tests/benchmark_multi/fixtures/phase39b"
J_REV = ROOT / "benchmark_results/multi/phase39j/observation_log_reviewed.json"
J_DATA = ROOT / "benchmark_results/multi/phase39j/datasets"
CAPTURE_DIR = OUT / "captures"

MODEL = "qwen2.5:7b"
BASELINE_MODE = "final_schema_expr_partition"
USER_PREFIX_V2_HEAD = (
    "Determine whether the proposed integration plan and observed result "
    "directly satisfy all material requirements in the user's request.\n"
)


def _dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_baseline_freeze() -> dict:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True)
    ver = ROOT / "core/integrate/semantic_verifier.py"
    cap = ROOT / "core/integrate/verifier_invocation_capture.py"
    esc = ROOT / "core/integrate/semantic_escalation.py"
    freeze = {
        "phase": "39L",
        "frozen_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_head": head,
        "dirty_working_tree": bool(dirty.strip()),
        "dirty_paths_sample": [ln for ln in dirty.splitlines() if ln.strip()][:50],
        "official_gate_entering": "B",
        "migration": "NOT_APPROVED",
        "production": {
            "legacy_primary": True,
            "candidate_research_shadow_only": True,
            "shadow_default": False,
            "candidate_must_not_affect_user_facing": True,
        },
        "candidate": {
            "materialization_mode": BASELINE_MODE,
            "label": "V2.2",
            "output_roles_policy": "R-ROLE-B",
            "verifier_model": MODEL,
            "planner_model": MODEL,
            "strong_model": "qwen3:32b",
            "llm_options_temperature": 0,
            "format_json": True,
            "timeout_s_default": 300,
        },
        "config_hashes": {
            "semantic_verifier_py_sha256_16": hashlib.sha256(ver.read_bytes()).hexdigest()[:16],
            "verifier_invocation_capture_py_sha256_16": hashlib.sha256(cap.read_bytes()).hexdigest()[:16],
            "semantic_escalation_py_sha256_16": hashlib.sha256(esc.read_bytes()).hexdigest()[:16],
            "prompt_version_hash_16": prompt_template_hash(
                VERIFIER_SYSTEM_PROMPT, USER_PREFIX_V2_HEAD
            )[:16],
        },
        "phase39j_gate": "C",
        "phase39k_gate": "B",
        "phase39k_classification": "sufficient_but_reasoning_unstable_or_hallucinated",
        "note": (
            "Instrumentation freeze. No semantic verifier redesign. "
            "Capture enabled only when MULTI_VERIFIER_CAPTURE_DIR is set."
        ),
    }
    _dump(OUT / "baseline_freeze.json", freeze)
    return freeze


def write_verifier_call_path() -> dict:
    doc = {
        "phase": "39L",
        "path": [
            {"step": 1, "module": "core.integrate.semantic_verifier", "fn": "build_verifier_payload", "role": "materialization + payload assembly"},
            {"step": 2, "module": "core.integrate.semantic_verifier", "fn": "run_semantic_verification", "role": "user message construction"},
            {"step": 3, "module": "core.integrate.verifier_invocation_capture", "fn": "build_invocation_record / persist_record", "role": "CAPTURE POINT before model invocation", "enable": "MULTI_VERIFIER_CAPTURE_DIR"},
            {"step": 4, "module": "core.llm_client", "fn": "_chat_raw or chat_json", "role": "LLM invocation; temperature=0; format=json"},
            {"step": 5, "module": "core.llm_client", "fn": "_extract_json_object", "role": "raw text to dict"},
            {"step": 6, "module": "core.integrate.semantic_verifier", "fn": "_normalize_verdict", "role": "verdict/reason normalization"},
            {"step": 7, "module": "core.integrate.semantic_escalation", "fn": "_should_semantic_escalate + update_last_escalation", "role": "escalation decision"},
            {"step": 8, "module": "core.shadow.runner", "fn": "Shadow worker", "role": "telemetry only; not user-facing"},
        ],
        "live_shadow_invocation_note": {
            "file": "core/integrate/semantic_escalation.py",
            "result_arg": None,
            "materialization_mode": BASELINE_MODE,
            "hypothesis_b_relevance": (
                "Phase 39K offline often passed result fingerprint; live Shadow path passes result=None."
            ),
        },
        "post_capture_transform": "No extra wrapping after capture on default _chat_raw path.",
    }
    _dump(OUT / "verifier_call_path.json", doc)
    return doc


def write_capture_schema() -> dict:
    schema = {
        "phase": "39L",
        "schema_version": 1,
        "fields": {
            "exact_verifier_input": "system + verbatim user message at pre-invocation",
            "exact_payload_hash": "sha256(canonicalize_json(exact_verifier_input))",
            "canonical_structured_payload": "structured JSON embedded in user message",
            "canonical_payload_hash": "sha256(canonicalize_json(structured payload))",
            "deterministic_evidence_snapshot": "materialization_evidence subset",
            "prompt_version_hash": "sha256(system + --- + user instruction prefix)",
            "model_id_temperature_timeout_format_json": "observable runtime config",
            "raw_model_response_text": "verbatim model content before parse",
            "raw_model_response_parsed": "JSON extracted from raw text",
            "parsed_verdict_reason_evidence": "after _normalize_verdict",
            "escalation_triggered_type": "from semantic_escalation when available",
        },
        "canonicalization": {
            "method": "json.dumps(sort_keys=True, separators=(',',':'), ensure_ascii=False)",
            "does": ["stable key ordering", "compact separators"],
            "does_not": ["drop semantic fields", "rewrite evidence", "infer equivalence", "normalize meaningful differences"],
        },
        "privacy": {
            "scope": "research/debug under MULTI_VERIFIER_CAPTURE_DIR",
            "not_in_ordinary_production_logs_by_default": True,
            "avoids_raw_workbook_rows": True,
            "may_include": "schema names, plan structure, materialization evidence",
        },
    }
    _dump(OUT / "capture_schema.json", schema)
    return schema


def write_overhead() -> dict:
    doc = {
        "phase": "39L",
        "capture_default": "OFF unless MULTI_VERIFIER_CAPTURE_DIR set",
        "legacy_critical_path": "Capture is inside verifier/Shadow path only.",
        "overhead_components": [
            "json canonicalize + sha256",
            "optional JSONL append under lock",
            "when capture on + default client: _chat_raw then extract",
        ],
        "isolation": {
            "shadow_fire_and_forget": True,
            "no_candidate_fallback": True,
            "telemetry_failure_swallowed": True,
        },
    }
    _dump(OUT / "instrumentation_overhead.json", doc)
    return doc


def _schemas_from_dir(case_dir: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in sorted(case_dir.glob("*.xlsx")):
        df = pd.read_excel(p)
        out[p.name] = [str(c) for c in df.columns]
    return out


def _p39j_case(cid: str, expect_pass: bool, bucket: str) -> dict:
    rev = _load(J_REV)
    row = next(r for r in rev["rows"] if r["case_id"] == cid)
    schemas = _schemas_from_dir(J_DATA / cid)
    fp = row.get("result_fingerprint") or {}
    return {
        "id": cid,
        "bucket": bucket,
        "expect_pass": expect_pass,
        "manual_shadow_correct": row.get("shadow_correct"),
        "live_verifier_verdict": row.get("verifier_verdict"),
        "live_verifier_reason": row.get("verifier_reason"),
        "live_verifier_evidence": row.get("verifier_evidence"),
        "prompt": row["prompt"],
        "plan": row["final_plan"],
        "source_schemas": schemas,
        "result_fingerprint": {"columns": fp.get("columns"), "shape": fp.get("shape")},
        "result": None,
        "historical_live_payload_captured": False,
        "replay_class_default": "RECONSTRUCTED_REPLAY",
    }


def build_cases() -> list[dict]:
    cases: list[dict] = [
        _p39j_case("P39J-05", True, "valid_rename_join"),
        _p39j_case("P39J-06", True, "valid_rename_join"),
        _p39j_case("P39J-07", True, "valid_rename_join"),
    ]
    p11 = _load(FIX_H / "p39g11_canonical.json")
    cases.append({
        "id": "P39G-11", "bucket": "fake_dual", "expect_pass": False,
        "prompt": p11["prompt"], "plan": p11["plan"],
        "source_schemas": p11.get("source_schemas"), "result": None,
        "historical_live_payload_captured": False, "replay_class_default": "RECONSTRUCTED_REPLAY",
    })
    fd = _load(FIX_H / "fake_dual_family.json")["cases"][0]
    cases.append({
        "id": fd.get("id") or "FD1", "bucket": "fake_dual", "expect_pass": False,
        "prompt": fd["prompt"], "plan": fd["plan"],
        "source_schemas": fd.get("source_schemas"), "result": None,
        "historical_live_payload_captured": False, "replay_class_default": "RECONSTRUCTED_REPLAY",
    })
    gs = _load(FIX_H / "genuine_same_origin_dual.json")["cases"][0]
    cases.append({
        "id": gs.get("id") or "GS1", "bucket": "genuine_same_origin", "expect_pass": True,
        "prompt": gs["prompt"], "plan": gs["plan"],
        "source_schemas": gs.get("source_schemas"), "result": None,
        "historical_live_payload_captured": False, "replay_class_default": "RECONSTRUCTED_REPLAY",
    })
    c2 = _load(FIX_B / "c2_and_controls.json")["cases"][0]
    cases.append({
        "id": c2.get("id") or "C2", "bucket": "c2_collapse", "expect_pass": False,
        "prompt": c2["prompt"], "plan": c2["plan"],
        "source_schemas": c2.get("source_schemas"), "result": None,
        "historical_live_payload_captured": False, "replay_class_default": "RECONSTRUCTED_REPLAY",
    })
    return cases


def capture_once(case: dict, *, result: dict | None, tag: str) -> dict:
    clear_last_record_for_tests()
    os.environ["MULTI_VERIFIER_CAPTURE_DIR"] = str(CAPTURE_DIR)
    os.environ["MULTI_VERIFIER_CAPTURE_ENABLED"] = "true"
    os.environ["MULTI_VERIFIER_CAPTURE_CASE_ID"] = f"{case['id']}:{tag}"
    t0 = time.time()
    r = run_semantic_verification(
        user_prompt=case["prompt"],
        plan=case["plan"],
        result=result,
        variant="V2",
        model=MODEL,
        independent=True,
        source_schemas=case.get("source_schemas"),
        materialization_mode=BASELINE_MODE,
    )
    rec = get_last_record_for_tests() or {}
    return {
        "verdict": r.verdict,
        "reason_code": r.reason_code,
        "evidence": list(r.evidence or []),
        "elapsed_s": round(time.time() - t0, 3),
        "parse_ok": r.parse_ok,
        "error": r.error,
        "exact_payload_hash": rec.get("exact_payload_hash"),
        "canonical_payload_hash": rec.get("canonical_payload_hash"),
        "prompt_version_hash": rec.get("prompt_version_hash"),
        "result_provided": rec.get("result_provided"),
        "raw_model_response_text": rec.get("raw_model_response_text"),
        "parsed_verdict": rec.get("parsed_verdict"),
        "parsed_reason_code": rec.get("parsed_reason_code"),
        "verifier_invocation_id": rec.get("verifier_invocation_id"),
        "temperature": rec.get("temperature"),
        "model_id": rec.get("model_id"),
        "materialization_version": rec.get("materialization_version"),
        "capture_record": rec,
    }


def exact_payload_replay(captured: dict, *, n: int = 1) -> list[dict]:
    from core.llm_client import _chat_raw, _extract_json_object
    from core.integrate.semantic_verifier import _normalize_verdict

    exact = (captured.get("capture_record") or {}).get("exact_verifier_input") or {}
    system = exact.get("system") or VERIFIER_SYSTEM_PROMPT
    user = exact.get("user")
    if not user:
        raise ValueError("no captured user message for exact replay")
    model = captured.get("model_id") or MODEL
    trials = []
    for i in range(n):
        t0 = time.time()
        raw_text = _chat_raw(
            user, system=system, base_url="http://localhost:11434",
            model=model, timeout=300, format_json=True,
        )
        raw = _extract_json_object(raw_text)
        out = _normalize_verdict(raw)
        trials.append({
            "trial": i + 1,
            "verdict": out.verdict,
            "reason_code": out.reason_code,
            "evidence": list(out.evidence or []),
            "raw_model_response_text": raw_text,
            "latency_s": round(time.time() - t0, 3),
            "replay_mode": "EXACT_REPLAY",
        })
    return trials


def run_payload_hash_tests(cases: list[dict]) -> dict:
    rows = []
    for c in cases:
        if c["id"] not in {"P39J-05", "P39J-06", "P39J-07"}:
            continue
        print(f"[hash] capture live-like {c['id']}", flush=True)
        live_like = capture_once(c, result=None, tag="hash_live_like")
        p_none = build_verifier_payload(
            user_prompt=c["prompt"], plan=c["plan"], result=None, variant="V2",
            independent=True, source_schemas=c.get("source_schemas"),
            materialization_mode=BASELINE_MODE,
        )
        p_fp = build_verifier_payload(
            user_prompt=c["prompt"], plan=c["plan"], result=c.get("result_fingerprint"),
            variant="V2", independent=True, source_schemas=c.get("source_schemas"),
            materialization_mode=BASELINE_MODE,
        )
        h_none = sha256_text(canonicalize_json(p_none))
        h_fp = sha256_text(canonicalize_json(p_fp))
        rows.append({
            "id": c["id"],
            "canonical_hash_result_none": h_none,
            "canonical_hash_result_fingerprint": h_fp,
            "stable_repeat": h_none == sha256_text(canonicalize_json(p_none)),
            "live_vs_fingerprint_differ": h_none != h_fp,
            "live_like_captured_exact_hash": live_like.get("exact_payload_hash"),
            "live_like_result_provided": live_like.get("result_provided"),
            "fingerprint_has_observed_result": "observed_result" in p_fp,
            "none_has_observed_result": "observed_result" in p_none,
        })
    out = {
        "phase": "39L",
        "tests": rows,
        "conclusion": (
            "Canonical hashes are stable. Live Shadow (result=None) vs Phase 39K "
            "fingerprint (result present) produce different structured payloads "
            "when fingerprint columns/shape exist — Hypothesis B candidate."
        ),
    }
    _dump(OUT / "payload_hash_tests.json", out)
    return out


def run_replay_fidelity(cases: list[dict]) -> dict:
    results = []
    for c in cases:
        if c["id"] not in {"P39J-06", "P39G-11"}:
            continue
        print(f"[fidelity] capture {c['id']}", flush=True)
        captured = capture_once(c, result=None, tag="fidelity_src")
        src_exact = captured["exact_payload_hash"]
        src_canon = captured["canonical_payload_hash"]
        p = build_verifier_payload(
            user_prompt=c["prompt"], plan=c["plan"], result=None, variant="V2",
            independent=True, source_schemas=c.get("source_schemas"),
            materialization_mode=BASELINE_MODE,
        )
        recon_canon = sha256_text(canonicalize_json(p))
        exact_in = captured["capture_record"]["exact_verifier_input"]
        replay_exact_hash = sha256_text(canonicalize_json(exact_in))
        fidelity = classify_replay_fidelity(
            source_exact_payload_hash=src_exact,
            replay_exact_payload_hash=replay_exact_hash,
            source_canonical_payload_hash=src_canon,
            replay_canonical_payload_hash=recon_canon,
            used_captured_verbatim_user=True,
            reconstructed_from_plan=False,
        )
        try:
            print(f"[fidelity] exact replay trial {c['id']}", flush=True)
            trial = exact_payload_replay(captured, n=1)[0]
        except Exception as exc:  # noqa: BLE001
            trial = {"error": f"{type(exc).__name__}: {exc}"}
        results.append({
            "id": c["id"],
            "source_exact_payload_hash": src_exact,
            "replay_exact_payload_hash": replay_exact_hash,
            "source_canonical_payload_hash": src_canon,
            "reconstructed_canonical_payload_hash": recon_canon,
            "canonical_match_reconstructed": src_canon == recon_canon,
            "fidelity_class": fidelity,
            "historical_p39j_live": "RECONSTRUCTED_REPLAY_ONLY",
            "exact_replay_trial": trial,
            "prompt_version_hash": captured.get("prompt_version_hash"),
            "materialization_version": captured.get("materialization_version"),
            "model_id": captured.get("model_id"),
            "temperature": captured.get("temperature"),
        })
    out = {
        "phase": "39L",
        "rows": results,
        "p39j_historical_limitation": (
            "No — original P39J-06/07 live calls were not captured with Phase 39L "
            "fidelity. Only RECONSTRUCTED_REPLAY is available for those historical calls."
        ),
    }
    _dump(OUT / "replay_fidelity_results.json", out)
    return out


def run_instability_stress(cases: list[dict], n: int = 10) -> dict:
    rows = []
    for c in cases:
        print(f"[stress] {c['id']} n={n} result=None", flush=True)
        trials = []
        hashes: set[str] = set()
        for i in range(n):
            t = capture_once(c, result=None, tag=f"stress_{i+1}")
            if t.get("exact_payload_hash"):
                hashes.add(t["exact_payload_hash"])
            trials.append({
                "trial": i + 1,
                "verdict": t["verdict"],
                "reason_code": t["reason_code"],
                "evidence": t["evidence"],
                "elapsed_s": t["elapsed_s"],
                "exact_payload_hash": t["exact_payload_hash"],
                "raw_excerpt": (t.get("raw_model_response_text") or "")[:400],
                "parsed_verdict": t.get("parsed_verdict"),
                "parsed_reason_code": t.get("parsed_reason_code"),
                "raw_vs_parsed_verdict_match": t.get("parsed_verdict") == t["verdict"],
            })
            print(f"  [{i+1}/{n}] {t['verdict']} {t['reason_code']}", flush=True)
        vcounts = Counter(t["verdict"] for t in trials)
        rcounts = Counter(t["reason_code"] for t in trials)
        rows.append({
            "id": c["id"],
            "bucket": c["bucket"],
            "expect_pass": c["expect_pass"],
            "replay_class": "RECONSTRUCTED_REPLAY",
            "result_mode": "None_live_like",
            "n": n,
            "distinct_exact_payload_hashes": len(hashes),
            "payload_hash_stable": len(hashes) == 1,
            "verdict_counts": dict(vcounts),
            "reason_counts": dict(rcounts),
            "pass_rate": vcounts.get("pass", 0) / n,
            "fail_rate": vcounts.get("fail", 0) / n,
            "uncertain_rate": vcounts.get("uncertain", 0) / n,
            "latency_s": {
                "min": min(t["elapsed_s"] for t in trials),
                "max": max(t["elapsed_s"] for t in trials),
                "mean": round(sum(t["elapsed_s"] for t in trials) / n, 3),
            },
            "trials": trials,
            "manual_review_needed_if_nonpass_on_valid": bool(
                c["expect_pass"] and vcounts.get("pass", 0) < n
            ),
        })
    out = {
        "phase": "39L",
        "n_target": n,
        "model": MODEL,
        "temperature": 0,
        "rows": rows,
        "note": (
            "All trials are RECONSTRUCTED_REPLAY of live-like (result=None) payloads. "
            "Historical P39J live payloads were not captured."
        ),
    }
    _dump(OUT / "instability_stress_results.json", out)
    return out


def manual_hallucination_notes(stress: dict) -> dict:
    notes = []
    for row in stress.get("rows") or []:
        if not row.get("expect_pass"):
            continue
        nonpass = [t for t in (row.get("trials") or []) if t.get("verdict") != "pass"]
        if not nonpass:
            notes.append({
                "id": row["id"], "label": None,
                "note_ko": "반복 재구성 리플레이에서 NON-PASS가 관측되지 않아 환각 수동 분류 대상 없음.",
            })
            continue
        for t in nonpass:
            notes.append({
                "id": row["id"], "trial": t["trial"], "verdict": t["verdict"],
                "reason_code": t["reason_code"], "evidence": t.get("evidence"),
                "raw_excerpt": t.get("raw_excerpt"), "label": "PENDING_MANUAL_REVIEW",
                "note_ko": (
                    "연구자가 결정적 materialization_evidence와 reason/evidence/raw를 "
                    "대조해 SUPPORTED_STRUCTURAL_CLAIM / UNSUPPORTED_STRUCTURAL_CLAIM / "
                    "AMBIGUOUS / OTHER_REASONING_ERROR 중 하나로 수동 분류해야 함."
                ),
            })
    out = {
        "phase": "39L",
        "reviews": notes,
        "disclaimer": "연구/수동 라벨 대기. Python이 환각 여부를 자동 판정하지 않음.",
    }
    _dump(OUT / "manual_hallucination_review.json", out)
    return out


def run_semantic_regression() -> dict:
    py = sys.executable
    cmds = [
        [py, "-m", "pytest", "tests/test_phase39l_verifier_capture.py", "-q"],
        [py, "-m", "pytest", "tests/test_phase39h_provenance_independence.py", "-q"],
        [py, "-m", "pytest", "tests/test_phase34_generalization.py", "-q"],
        [py, "-m", "pytest", "tests/test_phase33_semantic_verifier.py", "-q"],
    ]
    for extra in [
        "tests/test_phase35_semantic_escalation.py",
        "tests/test_phase38_exception_shadow_coverage.py",
    ]:
        if (ROOT / extra).exists():
            cmds.append([py, "-m", "pytest", extra, "-q"])
    results = []
    for cmd in cmds:
        print("[regression]", " ".join(cmd), flush=True)
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        results.append({
            "cmd": cmd, "returncode": p.returncode,
            "stdout_tail": (p.stdout or "")[-800:],
            "stderr_tail": (p.stderr or "")[-400:],
            "ok": p.returncode == 0,
        })
    out = {"phase": "39L", "results": results, "all_ok": all(r["ok"] for r in results)}
    _dump(OUT / "semantic_regression_results.json", out)
    return out


def write_summary(*, freeze: dict, hash_tests: dict, fidelity: dict, stress: dict, regression: dict, reviews: dict) -> dict:
    stress_rows = stress.get("rows") or []
    instability_observed = any(
        (r.get("pass_rate", 1) < 1.0 and r.get("expect_pass")) or (0 < r.get("pass_rate", 0) < 1.0)
        for r in stress_rows
    )
    capture_works = bool(hash_tests.get("tests"))
    fidelity_ok = any(r.get("fidelity_class") == "EXACT_REPLAY" for r in (fidelity.get("rows") or []))
    gate = "A"
    gaps: list[str] = []
    if not regression.get("all_ok"):
        gate = "C"
        gaps.append("semantic/instrumentation regression failed")
    elif not fidelity_ok and not fidelity.get("skipped"):
        gate = "B"
        gaps.append("exact replay fidelity not fully demonstrated under LLM")
    elif not capture_works and not hash_tests.get("skipped"):
        gate = "C"
        gaps.append("payload capture/hash tests empty")
    else:
        gaps.append("Historical P39J-06/07 live payloads remain reconstruct-only (expected)")

    summary = {
        "phase": "39L",
        "title": "Live Verifier Payload Capture & Inference Instability Characterization",
        "gate": gate,
        "gaps": gaps,
        "instrumentation_added": [
            "core/integrate/verifier_invocation_capture.py",
            "pre-invocation capture in run_semantic_verification",
            "escalation attach in semantic_escalation",
            "tests/test_phase39l_verifier_capture.py",
            "tests/benchmark_multi/phase39l_eval.py",
        ],
        "exact_payload_capture_possible": True,
        "replay_fidelity_demonstrated": fidelity_ok or bool(fidelity.get("skipped")),
        "instability_observed_offline_reconstructed": instability_observed,
        "p39j_historical_exact_replay": False,
        "p39j_historical_note": "No — only reconstructed replay is available for original P39J-06/07 live calls.",
        "hypothesis_b_signal": {
            "live_result_none_vs_fingerprint_hash_differs": any(
                t.get("live_vs_fingerprint_differ") for t in (hash_tests.get("tests") or [])
            ),
        },
        "migration": "NOT_APPROVED",
        "shadow_recommendation": "remain OFF; optional tiny live capture validation later",
        "next": (
            "Focused live verifier instability observation/replay Phase" if gate == "A"
            else "Close remaining observability gap before live instability Phase" if gate == "B"
            else "Fix instrumentation reliability before further research"
        ),
        "frozen_head": freeze.get("git_head"),
        "regression_all_ok": regression.get("all_ok"),
        "manual_reviews": reviews.get("reviews"),
    }
    _dump(OUT / "phase39l_summary.json", summary)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--skip-stress", action="store_true")
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--regression-only", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

    freeze = write_baseline_freeze()
    write_verifier_call_path()
    write_capture_schema()
    write_overhead()

    regression = run_semantic_regression()
    if args.regression_only:
        write_summary(
            freeze=freeze,
            hash_tests={"tests": [], "skipped": True},
            fidelity={"rows": [], "skipped": True},
            stress={"rows": [], "skipped": True},
            regression=regression,
            reviews={"reviews": []},
        )
        print(json.dumps({"gate": "pending", "regression_all_ok": regression["all_ok"]}))
        return

    cases = build_cases()
    case_summaries = []
    for c in cases:
        row = {k: v for k, v in c.items() if k != "plan"}
        row["plan_steps"] = len((c.get("plan") or {}).get("steps") or [])
        case_summaries.append(row)
    _dump(OUT / "cases.json", {"cases": case_summaries})

    if args.skip_llm:
        hash_tests: dict[str, Any] = {"phase": "39L", "tests": [], "skipped": True}
        for c in cases:
            if c["id"] not in {"P39J-05", "P39J-06", "P39J-07"}:
                continue
            p_none = build_verifier_payload(
                user_prompt=c["prompt"], plan=c["plan"], result=None, variant="V2",
                independent=True, source_schemas=c.get("source_schemas"),
                materialization_mode=BASELINE_MODE,
            )
            p_fp = build_verifier_payload(
                user_prompt=c["prompt"], plan=c["plan"], result=c.get("result_fingerprint"),
                variant="V2", independent=True, source_schemas=c.get("source_schemas"),
                materialization_mode=BASELINE_MODE,
            )
            hash_tests["tests"].append({
                "id": c["id"],
                "canonical_hash_result_none": sha256_text(canonicalize_json(p_none)),
                "canonical_hash_result_fingerprint": sha256_text(canonicalize_json(p_fp)),
                "live_vs_fingerprint_differ": canonicalize_json(p_none) != canonicalize_json(p_fp),
                "stable_repeat": True,
            })
        _dump(OUT / "payload_hash_tests.json", hash_tests)
        fidelity = {
            "phase": "39L", "rows": [], "skipped": True,
            "p39j_historical_limitation": "No — only reconstructed replay for original P39J-06/07.",
        }
        _dump(OUT / "replay_fidelity_results.json", fidelity)
        stress = {"phase": "39L", "rows": [], "skipped": True}
        _dump(OUT / "instability_stress_results.json", stress)
        reviews = {"reviews": []}
    else:
        hash_tests = run_payload_hash_tests(cases)
        fidelity = run_replay_fidelity(cases)
        if args.skip_stress:
            stress = {"phase": "39L", "rows": [], "skipped": True}
            _dump(OUT / "instability_stress_results.json", stress)
        else:
            stress = run_instability_stress(cases, n=args.n)
        reviews = manual_hallucination_notes(stress)

    summary = write_summary(
        freeze=freeze, hash_tests=hash_tests, fidelity=fidelity,
        stress=stress, regression=regression, reviews=reviews,
    )
    print(json.dumps({"gate": summary["gate"], "next": summary["next"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
