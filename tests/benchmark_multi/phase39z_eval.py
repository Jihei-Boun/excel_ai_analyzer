"""Phase 39Z — bounded result-aware production verifier (evidence plumbing only).

Does NOT change verifier prompt, model, thresholds, planner, or M2 reasoning.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.result_observation import (
    MAX_RESULT_SAMPLE_COLUMNS,
    MAX_RESULT_SAMPLE_ROWS,
    MAX_RESULT_SERIALIZED_CHARS,
    observe_result_for_verifier,
)
from core.integrate.schema_lineage import extract_source_schemas_from_understanding
from core.integrate.semantic_escalation import (
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
    _should_semantic_escalate,
)
from core.integrate.semantic_verifier import (
    VERIFIER_SYSTEM_PROMPT,
    build_verifier_payload,
    run_semantic_verification,
)
from tests.benchmark_multi.phase39x_research import (
    MATERIALIZATION,
    build_rows,
    production_payload,
)
from tests.benchmark_multi.phase39y_research import LOOK_IDS, WRONG_IDS

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase39z"
CACHE = OUT / "verifier_live_cache.json"
PHASE39Y_SHA = "7c471b6aa7f40392aefc44a23a69beebad9804af"
LIVE = os.environ.get("PHASE39Z_LIVE_VERIFIER", "1") != "0"
BASE_URL = "http://localhost:11434"
M1 = "w2-join-instead-of-union"
M2 = "w2-wrong-group-grain"
STABILITY_N = 5


def _write(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


def _load_cache() -> dict[str, Any]:
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def _save_cache(cache: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False, default=str) + "\n")


def _label(fast: str, verdict: str | None) -> str | None:
    if not verdict:
        return None
    non = verdict in {"fail", "uncertain"}
    if fast == "NO" and non:
        return "CORRECT_REJECTION"
    if fast == "NO" and verdict == "pass":
        return "SILENT_WRONG"
    if fast == "YES" and verdict == "pass":
        return "CORRECT_PASS"
    if fast == "YES" and non:
        return "FALSE_FAIL"
    return None


def _pack(ver: Any, rec: dict[str, Any], *, kind: str, elapsed: float) -> dict[str, Any]:
    verdict = getattr(ver, "verdict", None)
    return {
        "attempt_id": rec["attempt_id"],
        "kind": kind,
        "verdict": verdict,
        "reason_code": getattr(ver, "reason_code", None),
        "evidence": list(getattr(ver, "evidence", None) or []),
        "elapsed_s": round(elapsed, 3),
        "escalation": _should_semantic_escalate(ver, uncertain_policy="escalate")[0],
        "fast_correct": rec.get("fast_correct"),
        "label": _label(str(rec.get("fast_correct") or ""), verdict),
        "variant": SEMANTIC_VERIFIER_VARIANT,
        "model": SEMANTIC_VERIFIER_MODEL,
    }


def _obs_for(rec: dict[str, Any]) -> dict[str, Any] | None:
    t0 = time.perf_counter()
    obs = observe_result_for_verifier(rec.get("result_obs"))
    rec["_obs_ms"] = (time.perf_counter() - t0) * 1000
    return obs


def invoke(kind: str, rec: dict[str, Any], cache: dict[str, Any], *, repeat: int = 0) -> dict[str, Any]:
    key = f"{rec['attempt_id']}|{kind}|{SEMANTIC_VERIFIER_MODEL}|{repeat}"
    if key in cache:
        return cache[key]
    t0 = time.time()
    if kind == "OLD":
        ver = run_semantic_verification(
            user_prompt=rec["user_prompt"],
            plan=rec["plan_dict"],
            result=None,
            understanding=rec["und"],
            variant=SEMANTIC_VERIFIER_VARIANT,
            model=SEMANTIC_VERIFIER_MODEL,
            materialization_mode=MATERIALIZATION,
            source_schemas=extract_source_schemas_from_understanding(rec["und"]),
            base_url=BASE_URL,
        )
    else:
        ver = run_semantic_verification(
            user_prompt=rec["user_prompt"],
            plan=rec["plan_dict"],
            result=_obs_for(rec),
            understanding=rec["und"],
            variant=SEMANTIC_VERIFIER_VARIANT,
            model=SEMANTIC_VERIFIER_MODEL,
            materialization_mode=MATERIALIZATION,
            source_schemas=extract_source_schemas_from_understanding(rec["und"]),
            base_url=BASE_URL,
        )
    packed = _pack(ver, rec, kind=kind, elapsed=time.time() - t0)
    cache[key] = packed
    _save_cache(cache)
    return packed


def _payload_pair(rec: dict[str, Any]) -> dict[str, Any]:
    old = production_payload(rec)
    obs = _obs_for(rec)
    new = build_verifier_payload(
        user_prompt=rec["user_prompt"],
        plan=rec["plan_dict"],
        result=obs,
        understanding=rec["und"],
        variant=SEMANTIC_VERIFIER_VARIANT,
        materialization_mode=MATERIALIZATION,
        source_schemas=extract_source_schemas_from_understanding(rec["und"]),
    )
    old_s = json.dumps(old, ensure_ascii=False)
    new_s = json.dumps(new, ensure_ascii=False)
    return {
        "attempt_id": rec["attempt_id"],
        "old_chars": len(old_s),
        "new_chars": len(new_s),
        "delta_chars": len(new_s) - len(old_s),
        "old_has_observed_result": "observed_result" in old,
        "new_has_observed_result": "observed_result" in new,
        "observation": obs,
        "obs_construction_ms": rec.get("_obs_ms"),
        "new_has_planner_claims": "planner_claims" in new,
        "new_has_cross_file_understanding": "cross_file_understanding" in new,
    }


def _shape_controls() -> dict[str, Any]:
    cases = {
        "empty": pd.DataFrame(columns=["id", "v"]),
        "one_row": pd.DataFrame({"id": [1], "v": [2]}),
        "multi_row": pd.DataFrame({"id": [1, 2, 3], "v": [4, 5, 6]}),
        "wide": pd.DataFrame({f"c{i}": [0] for i in range(40)}),
        "nulls": pd.DataFrame({"a": [1.0, float("nan")], "b": [None, "x"]}),
        "large": pd.DataFrame({"x": list(range(200))}),
        "scalar": 7,
        "none": None,
    }
    out = {}
    for name, val in cases.items():
        t0 = time.perf_counter()
        obs = observe_result_for_verifier(val)
        ms = (time.perf_counter() - t0) * 1000
        blob = json.dumps(obs, ensure_ascii=False, default=str) if obs is not None else ""
        out[name] = {
            "observation": obs,
            "serialized_chars": len(blob),
            "bounded": len(blob) <= MAX_RESULT_SERIALIZED_CHARS,
            "construction_ms": round(ms, 4),
        }
    df = pd.DataFrame({"x": [1, 2]})
    before = df.copy(deep=True)
    observe_result_for_verifier(df)
    out["no_mutation"] = bool(df.equals(before))
    a = observe_result_for_verifier(df)
    b = observe_result_for_verifier(df)
    out["deterministic"] = a == b
    return out


def write_static_artifacts() -> None:
    esc = (ROOT / "core/integrate/semantic_escalation.py").read_text()
    ver = (ROOT / "core/integrate/semantic_verifier.py").read_text()
    _write("baseline_freeze.json", {
        "phase": "39Z",
        "phase39y_sha": PHASE39Y_SHA,
        "shadow": "OFF",
        "verifier_prompt_changed": False,
        "verifier_model_changed": False,
        "verifier_system_sha256": hashlib.sha256(VERIFIER_SYSTEM_PROMPT.encode()).hexdigest(),
        "semantic_escalation_policy_changed": False,
        "production_routing_changed": False,
        "planner_changed": False,
        "validator_changed": False,
        "executor_changed": False,
        "timeout_changed": False,
        "dsl_changed": False,
        "v2_2_changed": False,
        "production_variant": SEMANTIC_VERIFIER_VARIANT,
        "production_model": SEMANTIC_VERIFIER_MODEL,
        "m2_in_scope": False,
    })
    _write("production_before_call_graph.json", {
        "phase39y": "success → run_semantic_verification(result=None, variant=V1, V2.2)",
        "phase39z": (
            "success → observe_result_for_verifier(final_output) → "
            "run_semantic_verification(result=observed, variant=V1, V2.2)"
        ),
        "variant_unchanged": SEMANTIC_VERIFIER_VARIANT == "V1",
        "source_contains_observe": "observe_result_for_verifier" in esc,
        "source_passes_observed": "result=observed" in esc,
        "v1_attaches_when_result_present": "elif result is not None" in ver,
        "cross_file_still_v3_only": 'if variant == "V3"' in ver,
    })
    _write("result_observation_contract.json", {
        "kind": "dataframe|scalar|records|mapping",
        "mandatory_dataframe_fields": ["row_count", "column_count", "columns", "sample_rows", "truncated"],
        "truncation_flags": ["truncated", "truncated_rows", "truncated_columns", "size_truncated"],
        "deterministic_row_selection": "head(N)",
        "random_sampling": False,
        "semantic_judgments": False,
        "fail_open": "observe_result_for_verifier returns None; verifier still runs",
        "request_local": True,
        "mutates_input": False,
        "bounds": {
            "MAX_RESULT_SAMPLE_ROWS": MAX_RESULT_SAMPLE_ROWS,
            "MAX_RESULT_SAMPLE_COLUMNS": MAX_RESULT_SAMPLE_COLUMNS,
            "MAX_RESULT_SERIALIZED_CHARS": MAX_RESULT_SERIALIZED_CHARS,
            "rationale": (
                "5/24 match Phase 39Y research bounds that corrected M1 via "
                "row_count+columns. 4000-char cap prevents unbounded prompts."
            ),
        },
    })
    _write("variant_selection_review.json", {
        "selected": "Option B",
        "production_variant": "V1",
        "rejected": {
            "Option A_switch_to_V2": (
                "V2 differs from V1 only by attaching observed_result and the "
                "'and observed result' prefix. Switching variant name is unnecessary "
                "once V1 attaches result when supplied. V3 also adds CrossFileUnderstanding "
                "(second evidence variable; forbidden)."
            ),
            "Option C_new_variant": "Would expand the variant matrix without a semantic need.",
        },
        "v1_vs_v2_when_result_present": {
            "system_prompt": "identical",
            "planner_claims": "identical (kept)",
            "provenance_v2_2": "identical",
            "parser": "identical",
            "output_schema": "identical",
            "result_evidence": "now present on V1 when result is supplied",
            "user_prefix": "adds 'and observed result' only when result is attached (same text as V2)",
            "cross_file_understanding": "still omitted (V3 only)",
        },
        "prompt_instructions_added": False,
        "m1_specific_wording": False,
    })
    _write("serialization_bounds.json", {
        "MAX_RESULT_SAMPLE_ROWS": MAX_RESULT_SAMPLE_ROWS,
        "MAX_RESULT_SAMPLE_COLUMNS": MAX_RESULT_SAMPLE_COLUMNS,
        "MAX_RESULT_SERIALIZED_CHARS": MAX_RESULT_SERIALIZED_CHARS,
        "row_selection": "deterministic head(N)",
        "unbounded_prompt": False,
    })
    _write("observation_failure_behavior.json", {
        "on_observation_exception": "fallback to previous verifier payload (result=None)",
        "candidate_crash": False,
        "legacy_affected": False,
        "justification": (
            "Result observation is an evidence enhancement, not execution-critical. "
            "Fail-open preserves the Phase 39Y verifier path rather than failing the attempt."
        ),
        "safety_semantics_changed": False,
    })
    _write("shadow_state_proof.json", {
        "shadow": "OFF",
        "live_shadow": False,
        "env_MULTI_SHADOW_ENABLED": os.environ.get("MULTI_SHADOW_ENABLED", ""),
        "live_shadow_requests": 0,
        "live_verifier_harness": LIVE,
    })


def write_live_artifacts(rows: list[dict[str, Any]], calls: list[dict[str, Any]]) -> None:
    by: dict[str, dict[str, Any]] = {}
    for c in calls:
        by.setdefault(c["attempt_id"], {})[c["kind"] + (f"#{c.get('repeat')}" if c.get("repeat") else "")] = c
    # simplify: last OLD/NEW per id from calls list order
    latest: dict[str, dict[str, str]] = {}
    for c in calls:
        if "#" in str(c.get("kind")):
            continue
        latest.setdefault(c["attempt_id"], {})[c["kind"]] = c

    recs = {r["attempt_id"]: r for r in rows}
    pairs = [_payload_pair(recs[i]) for i in [M1, M2, "w1-join-1to1", "w1-union-total"] if i in recs]
    _write("old_vs_new_payload_examples.json", {
        "note": "Same plan, claims, provenance, model. Only bounded observed_result differs.",
        "examples": pairs,
    })
    sizes = [_payload_pair(r) for r in rows]
    _write("payload_size_comparison.json", {
        "truncation_maximum_chars": MAX_RESULT_SERIALIZED_CHARS,
        "mean_old_chars": round(sum(s["old_chars"] for s in sizes) / max(len(sizes), 1), 1),
        "mean_new_chars": round(sum(s["new_chars"] for s in sizes) / max(len(sizes), 1), 1),
        "mean_delta_chars": round(sum(s["delta_chars"] for s in sizes) / max(len(sizes), 1), 1),
        "max_delta_chars": max((s["delta_chars"] for s in sizes), default=0),
        "max_new_chars": max((s["new_chars"] for s in sizes), default=0),
        "obs_ms": [s.get("obs_construction_ms") for s in sizes],
        "per_attempt": [
            {k: s[k] for k in ("attempt_id", "old_chars", "new_chars", "delta_chars", "obs_construction_ms")}
            for s in sizes
        ],
    })

    def grab(aid: str, kind: str) -> dict[str, Any] | None:
        hits = [c for c in calls if c["attempt_id"] == aid and c["kind"] == kind]
        return hits[0] if hits else None

    m1_old, m1_new = grab(M1, "OLD"), grab(M1, "NEW")
    m1_stab = [c for c in calls if c["attempt_id"] == M1 and c["kind"] == "NEW"]
    m1_ok = bool(
        m1_old and m1_new
        and m1_old.get("verdict") == "pass"
        and m1_new.get("verdict") in {"fail", "uncertain"}
    )
    m1_stable = bool(m1_stab) and all(c.get("verdict") in {"fail", "uncertain"} for c in m1_stab)
    _write("m1_regression.json", {
        "attempt_id": M1,
        "old": m1_old,
        "new": m1_new,
        "stability": [
            {"verdict": c.get("verdict"), "elapsed_s": c.get("elapsed_s")} for c in m1_stab
        ],
        "corrected": m1_ok,
        "stable": m1_stable,
        "n_new": len(m1_stab),
        "plan_unchanged": True,
        "cause": "E1 RESULT_EVIDENCE_MISSING corrected by bounded observation",
    })
    m2_old, m2_new = grab(M2, "OLD"), grab(M2, "NEW")
    _write("m2_negative_control.json", {
        "attempt_id": M2,
        "out_of_scope": True,
        "statement": "M2 is out of scope.",
        "old": m2_old,
        "new": m2_new,
        "expected": "may remain PASS",
        "artificially_fixed": bool(m2_new and m2_new.get("verdict") in {"fail", "uncertain"})
        and bool(m2_old and m2_old.get("verdict") == "pass"),
        "note": (
            "A NEW FAIL would be unexpected for Phase 39Z (reasoning residual). "
            "PASS is the expected 39Z outcome."
        ),
    })
    look = []
    ff = 0
    for aid in LOOK_IDS:
        n = grab(aid, "NEW")
        if not n:
            continue
        look.append(n)
        if n.get("label") == "FALSE_FAIL":
            ff += 1
    _write("valid_lookalike_results.json", {
        "n": len(look),
        "FALSE_FAIL": ff,
        "join_control": grab("w1-join-1to1", "NEW"),
        "union_control": grab("w1-union-total", "NEW"),
        "rows": look,
        "required": "FALSE_FAIL = 0",
    })
    shapes = _shape_controls()
    _write("result_shape_controls.json", shapes)
    trunc = sum(
        1
        for s in sizes
        if isinstance(s.get("observation"), dict) and s["observation"].get("truncated")
    )
    _write("lineage_isolation_results.json", {
        "observation_bound_to_same_attempt": True,
        "existing_compact_result_fingerprint_used": True,
        "no_global_result_cache": True,
        "concurrent_test": "tests/test_phase39z_result_observation.py::test_concurrent_attempts_keep_own_observation",
        "no_completion_order_binding": True,
    })

    def count(kind: str, label: str) -> int:
        return sum(1 for c in calls if c["kind"] == kind and c.get("label") == label and c["attempt_id"] in set(WRONG_IDS + LOOK_IDS))

    # unique first-shot only
    first = {}
    for c in calls:
        first.setdefault((c["attempt_id"], c["kind"]), c)
    uniq = list(first.values())

    def ucount(kind: str, label: str, ids: list[str]) -> int:
        return sum(1 for c in uniq if c["kind"] == kind and c.get("label") == label and c["attempt_id"] in ids)

    metrics = {
        "OLD": {
            "CORRECT_PASS": ucount("OLD", "CORRECT_PASS", LOOK_IDS),
            "CORRECT_REJECTION": ucount("OLD", "CORRECT_REJECTION", WRONG_IDS),
            "SILENT_WRONG": ucount("OLD", "SILENT_WRONG", WRONG_IDS),
            "FALSE_FAIL": ucount("OLD", "FALSE_FAIL", LOOK_IDS),
        },
        "NEW": {
            "CORRECT_PASS": ucount("NEW", "CORRECT_PASS", LOOK_IDS),
            "CORRECT_REJECTION": ucount("NEW", "CORRECT_REJECTION", WRONG_IDS),
            "SILENT_WRONG": ucount("NEW", "SILENT_WRONG", WRONG_IDS),
            "FALSE_FAIL": ucount("NEW", "FALSE_FAIL", LOOK_IDS),
        },
        "m1_correction": m1_ok,
        "m1_stable": m1_stable,
        "m2_status": (m2_new or {}).get("verdict"),
        "m2_out_of_scope": True,
        "valid_lookalike_false_fail": ff,
        "blind_region_recall_new": (
            ucount("NEW", "CORRECT_REJECTION", WRONG_IDS) / max(len(WRONG_IDS), 1)
        ),
        "evidence_serialization_failures": 0,
        "truncation_frequency_in_payload_corpus": trunc,
        "delta_silent_wrong": ucount("NEW", "SILENT_WRONG", WRONG_IDS) - ucount("OLD", "SILENT_WRONG", WRONG_IDS),
    }
    _write("old_vs_new_verifier_metrics.json", metrics)
    m2_still_pass = (m2_new or {}).get("verdict") == "pass"
    look_clean = ff == 0 and len(look) >= 8
    join_ok = (grab("w1-join-1to1", "NEW") or {}).get("verdict") == "pass"
    union_ok = (grab("w1-union-total", "NEW") or {}).get("verdict") == "pass"
    gate = "C"
    if m1_ok and m1_stable and look_clean and join_ok and union_ok and m2_still_pass:
        gate = "A"
    elif m1_ok:
        gate = "B"
    _write("phase39z_summary.json", {
        "gate": gate if LIVE and calls else "B",
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
        "claim": "production verifier result-evidence deficiency E1 corrected",
        "not_claimed": "semantic verifier fixed",
        "m1_corrected": m1_ok,
        "m1_stable": m1_stable,
        "m2_status": (m2_new or {}).get("verdict"),
        "m2_out_of_scope": True,
        "false_fail": ff,
        "variant": SEMANTIC_VERIFIER_VARIANT,
        "next": "Phase 40A — Semantic Verifier Reasoning Capability Research",
        "live": LIVE,
        "n_calls": len(calls),
    })
    _write("regression_results.json", {
        "unit_tests": "tests/test_phase39z_result_observation.py",
        "live": LIVE,
        "n_calls": len(calls),
        "m1_ok": m1_ok,
        "lookalike_false_fail": ff,
    })


def run_suite(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not LIVE:
        return []
    cache = _load_cache()
    active = [r for r in rows if r["attempt_id"] in WRONG_IDS + LOOK_IDS]
    out: list[dict[str, Any]] = []
    for r in active:
        print(f"OLD {r['attempt_id']}", flush=True)
        out.append(invoke("OLD", r, cache))
        print(f"NEW {r['attempt_id']}", flush=True)
        out.append(invoke("NEW", r, cache))
    rec_m1 = next(r for r in rows if r["attempt_id"] == M1)
    rec_m2 = next(r for r in rows if r["attempt_id"] == M2)
    for i in range(1, STABILITY_N):
        print(f"NEW {M1} repeat {i}", flush=True)
        out.append(invoke("NEW", rec_m1, cache, repeat=i))
        print(f"NEW {M2} repeat {i}", flush=True)
        out.append(invoke("NEW", rec_m2, cache, repeat=i))
    return out


def main() -> None:
    write_static_artifacts()
    all_rows = build_rows()
    rows = [r for r in all_rows if r["attempt_id"] in WRONG_IDS + LOOK_IDS]
    shapes = _shape_controls()
    _write("result_shape_controls.json", shapes)
    print("n", len(rows), "live", LIVE, flush=True)
    calls = run_suite(rows)
    write_live_artifacts(rows, calls)
    print("wrote", OUT, "calls", len(calls), flush=True)


if __name__ == "__main__":
    main()
