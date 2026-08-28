"""Phase 39K — wrong_output_grain false-fail root cause & evidence ablation.

Offline diagnostic harness only.
Does not enable Shadow, does not migrate, does not patch production mid-run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.schema_lineage import build_schema_lineage
from core.integrate.semantic_verifier import (
    VERIFIER_SYSTEM_PROMPT,
    assert_no_golden_leakage,
    build_verifier_payload,
    _normalize_verdict,
    run_semantic_verification,
)
from core.llm_client import chat_json

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase39k"
FIX_H = ROOT / "tests/benchmark_multi/fixtures/phase39h"
FIX_F = ROOT / "tests/benchmark_multi/fixtures/phase39f"
FIX_B = ROOT / "tests/benchmark_multi/fixtures/phase39b"
J_REV = ROOT / "benchmark_results/multi/phase39j/observation_log_reviewed.json"
J_DATA = ROOT / "benchmark_results/multi/phase39j/datasets"

MODEL = "qwen2.5:7b"
BASELINE_MODE = "final_schema_expr_partition"


def _dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_baseline_freeze() -> dict:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True)
    ver = ROOT / "core/integrate/semantic_verifier.py"
    lin = ROOT / "core/integrate/schema_lineage.py"
    freeze = {
        "phase": "39K",
        "frozen_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_head": head,
        "dirty_working_tree": bool(dirty.strip()),
        "dirty_paths_sample": [ln for ln in dirty.splitlines() if ln.strip()][:40],
        "candidate": {
            "materialization_mode": BASELINE_MODE,
            "label": "V2.2",
            "output_roles_policy": "R-ROLE-B",
            "verifier_model": MODEL,
            "planner_model": MODEL,
            "strong_model": "qwen3:32b",
            "legacy_primary": True,
            "shadow_default": False,
            "migration": "NOT_APPROVED",
        },
        "config_hashes": {
            "semantic_verifier_py_sha256_16": hashlib.sha256(ver.read_bytes()).hexdigest()[:16],
            "schema_lineage_py_sha256_16": hashlib.sha256(lin.read_bytes()).hexdigest()[:16],
        },
        "phase39j_gate": "C",
        "phase39j_stop": ["STOP-5"],
        "confirmed_materialization": BASELINE_MODE,
        "note": "Diagnostic freeze. No production candidate mutation in 39K baseline.",
    }
    _dump(OUT / "baseline_freeze.json", freeze)
    return freeze


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
        "result": {"columns": fp.get("columns"), "shape": fp.get("shape")},
    }


def build_reproduction_cases() -> list[dict]:
    cases: list[dict] = [
        _p39j_case("P39J-05", True, "valid_rename_join_pass_comparator"),
        _p39j_case("P39J-06", True, "valid_rename_join_ff_anchor"),
        _p39j_case("P39J-07", True, "valid_rename_join_ff_anchor"),
    ]
    p14 = FIX_F / "p39e14_frozen.json"
    if p14.exists():
        obj = _load(p14)
        cases.append(
            {
                "id": "P39E-14",
                "bucket": "valid_dual_historical",
                "expect_pass": True,
                "prompt": obj["prompt"],
                "plan": obj["plan"],
                "source_schemas": obj.get("source_schemas"),
                "result": None,
            }
        )
    for c in _load(FIX_H / "fake_dual_family.json")["cases"][:4]:
        cases.append(
            {
                "id": c["id"],
                "bucket": "fake_dual",
                "expect_pass": False,
                "prompt": c["prompt"],
                "plan": c["plan"],
                "source_schemas": c.get("source_schemas"),
                "result": None,
            }
        )
    p11 = _load(FIX_H / "p39g11_canonical.json")
    cases.append(
        {
            "id": "P39G-11",
            "bucket": "fake_dual",
            "expect_pass": False,
            "prompt": p11["prompt"],
            "plan": p11["plan"],
            "source_schemas": p11.get("source_schemas"),
            "result": None,
        }
    )
    for c in _load(FIX_H / "genuine_same_origin_dual.json")["cases"][:3]:
        cases.append(
            {
                "id": c["id"],
                "bucket": "genuine_same_origin",
                "expect_pass": True,
                "prompt": c["prompt"],
                "plan": c["plan"],
                "source_schemas": c.get("source_schemas"),
                "result": None,
            }
        )
    c2_path = FIX_B / "c2_and_controls.json"
    if c2_path.exists():
        for c in (_load(c2_path).get("cases") or [])[:4]:
            exp = False
            if c.get("expected_verdict") == "pass" or c.get("expect_pass") is True:
                exp = True
            if c.get("expected_verdict_not") == "pass":
                exp = False
            if "expect_pass" in c:
                exp = bool(c["expect_pass"])
            cases.append(
                {
                    "id": c.get("id") or "C2",
                    "bucket": "c2_collapse",
                    "expect_pass": exp,
                    "prompt": c["prompt"],
                    "plan": c["plan"],
                    "source_schemas": c.get("source_schemas"),
                    "result": None,
                }
            )
    return cases


def structural_facts(case: dict) -> dict:
    plan = case.get("plan") or {}
    schemas = case.get("source_schemas") or {}
    ev = build_schema_lineage(plan, schemas)
    ops = [s.get("op") for s in (plan.get("steps") or []) if isinstance(s, dict)]
    final_schema = list(ev.get("final_schema") or [])
    origins = ev.get("final_column_origins") or {}
    identical = ev.get("identical_evidence_signature_column_sets") or []
    join_keys: set[str] = set()
    for s in plan.get("steps") or []:
        if not isinstance(s, dict) or s.get("op") != "join":
            continue
        params = s.get("params") or {}
        join_keys.update(str(x) for x in (params.get("left_keys") or []))
        join_keys.update(str(x) for x in (params.get("right_keys") or []))
    metric_cols = [c for c in final_schema if c not in join_keys]
    result_cols = (case.get("result") or {}).get("columns")
    return {
        "ops_sequence": ops,
        "aggregate_op_count": sum(1 for o in ops if o == "aggregate"),
        "union_op_count": sum(1 for o in ops if o in {"union_rows", "union"}),
        "final_schema": final_schema,
        "final_column_origins": origins,
        "final_column_evidence_signatures": ev.get("final_column_evidence_signatures") or {},
        "identical_evidence_signature_column_sets": identical,
        "equivalent_evidence_signature_groups": ev.get("equivalent_evidence_signature_groups") or [],
        "join_keys_observed": sorted(join_keys),
        "metric_columns_observed": metric_cols,
        "metrics_share_identical_signature_group": any(
            len(set(g) & set(metric_cols)) >= 2 for g in identical
        ),
        "result_columns_observed": result_cols,
        "result_shape_observed": (case.get("result") or {}).get("shape"),
        "result_schema_matches_materialization": (
            None if result_cols is None else list(result_cols) == list(final_schema)
        ),
    }


def _ok(expect_pass: bool, verdict: str) -> bool:
    return (verdict == "pass") if expect_pass else (verdict != "pass")


def verify_baseline(case: dict) -> dict:
    t0 = time.time()
    r = run_semantic_verification(
        user_prompt=case["prompt"],
        plan=case["plan"],
        result=case.get("result"),
        variant="V2",
        model=MODEL,
        independent=True,
        source_schemas=case.get("source_schemas"),
        materialization_mode=BASELINE_MODE,
    )
    return {
        "verdict": r.verdict,
        "reason_code": r.reason_code,
        "evidence": list(r.evidence or []),
        "elapsed_s": round(time.time() - t0, 3),
        "parse_ok": r.parse_ok,
        "error": r.error,
        "mode": "V2.2_baseline",
    }


def _augment_payload(payload: dict, case: dict, candidate: str) -> dict:
    p = deepcopy(payload)
    facts = structural_facts(case)
    me = p.setdefault("materialization_evidence", {})
    if candidate in {"A_op_boundary", "A_plus_B"}:
        me["operation_boundary_summary"] = {
            "ops_sequence": facts["ops_sequence"],
            "aggregate_op_count": facts["aggregate_op_count"],
            "union_op_count": facts["union_op_count"],
            "join_keys_observed": facts["join_keys_observed"],
            "metric_columns_in_final_schema": facts["metric_columns_observed"],
            "metrics_share_identical_signature_group": facts[
                "metrics_share_identical_signature_group"
            ],
            "note": (
                "Deterministic operation/schema observations only. "
                "Not a semantic pass/fail judgment."
            ),
        }
        me["salient_final_schema"] = facts["final_schema"]
        if facts["result_columns_observed"] is not None:
            me["observed_result_columns"] = facts["result_columns_observed"]
            me["observed_result_shape"] = facts["result_shape_observed"]
            me["result_matches_final_schema"] = facts[
                "result_schema_matches_materialization"
            ]
    if candidate in {"B_no_planner_claims", "A_plus_B"}:
        p["planner_claims"] = {
            "_ablation_note": "planner_claims withheld to test claim-contamination"
        }
    return p


def verify_candidate(case: dict, candidate: str) -> dict:
    payload = build_verifier_payload(
        user_prompt=case["prompt"],
        plan=case["plan"],
        result=case.get("result"),
        understanding=None,
        variant="V2",
        independent=True,
        source_schemas=case.get("source_schemas"),
        materialization_mode=BASELINE_MODE,
    )
    payload = _augment_payload(payload, case, candidate)
    assert_no_golden_leakage(payload)
    user = (
        "Determine whether the proposed integration plan and observed result "
        "directly satisfy all material requirements in the user's request.\n"
        "Step order (mandatory):\n"
        "  (1) Reconstruct material requirements from user_prompt only.\n"
        "  (2) Decide from plan_structure + materialization_evidence.\n"
        "  (3) Optionally glance at planner_claims — never as proof.\n"
        "Do not invent operations or columns absent from materialization_evidence.\n"
        "If final_schema retains multiple distinct metric columns and "
        "operation_boundary_summary.aggregate_op_count is 0, do not claim a "
        "collapsed total column exists.\n"
        "Do not repair the plan. If evidence is insufficient, return uncertain.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    t0 = time.time()
    try:
        raw = chat_json(
            user,
            system=VERIFIER_SYSTEM_PROMPT,
            base_url="http://localhost:11434",
            model=MODEL,
        )
        r = _normalize_verdict(raw if isinstance(raw, dict) else {})
        return {
            "verdict": r.verdict,
            "reason_code": r.reason_code,
            "evidence": list(r.evidence or []),
            "elapsed_s": round(time.time() - t0, 3),
            "parse_ok": r.parse_ok,
            "error": r.error,
            "mode": candidate,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "verdict": "parse_failed",
            "reason_code": None,
            "evidence": [],
            "elapsed_s": round(time.time() - t0, 3),
            "parse_ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "mode": candidate,
        }


def run_stability(cases: list[dict], n: int = 5) -> dict:
    want = {"P39J-05", "P39J-06", "P39J-07", "P39G-11", "GS1", "FD1"}
    targets = [c for c in cases if c["id"] in want]
    collapse = [c for c in cases if c["bucket"] == "c2_collapse" and not c["expect_pass"]]
    if collapse:
        targets.append(collapse[0])
    seen: set[str] = set()
    uniq: list[dict] = []
    for c in targets:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        uniq.append(c)

    out: dict[str, Any] = {"n": n, "cases": {}}
    for c in uniq:
        print(f"\n[stability] {c['id']} x{n}", flush=True)
        runs = []
        for i in range(n):
            r = verify_baseline(c)
            runs.append(r)
            print(f"  {i+1}/{n} {r['verdict']} {r['reason_code']} {r['elapsed_s']}s", flush=True)
        counts = Counter(r["verdict"] for r in runs)
        out["cases"][c["id"]] = {
            "bucket": c["bucket"],
            "expect_pass": c["expect_pass"],
            "verdict_counts": dict(counts),
            "reason_counts": dict(Counter(r.get("reason_code") for r in runs)),
            "runs": runs,
            "stable_fail": counts.get("fail", 0) == n,
            "stable_pass": counts.get("pass", 0) == n,
            "unstable": len(counts) > 1,
        }
    return out


def run_ablation_matrix(cases: list[dict], n: int = 1) -> dict:
    modes = ["V2.2_baseline", "A_op_boundary", "B_no_planner_claims", "A_plus_B"]
    focus_ids = {
        "P39J-05", "P39J-06", "P39J-07", "P39E-14", "P39G-11", "GS1", "GS2", "FD1", "FD2"
    }
    focus = [c for c in cases if c["id"] in focus_ids]
    negs = [c for c in cases if c["bucket"] == "c2_collapse" and not c["expect_pass"]][:2]
    focus.extend(negs)

    matrix: dict[str, Any] = {"modes": modes, "rows": []}
    by_mode: dict[str, list] = {m: [] for m in modes}

    for c in focus:
        row: dict[str, Any] = {
            "id": c["id"],
            "bucket": c["bucket"],
            "expect_pass": c["expect_pass"],
            "modes": {},
        }
        for m in modes:
            print(f"[ablation] {c['id']} {m}", flush=True)
            trials = []
            for _ in range(n):
                if m == "V2.2_baseline":
                    trials.append(verify_baseline(c))
                else:
                    trials.append(verify_candidate(c, m))
            verdicts = [t["verdict"] for t in trials]
            maj = Counter(verdicts).most_common(1)[0][0]
            ok = _ok(c["expect_pass"], maj)
            row["modes"][m] = {
                "verdict_majority": maj,
                "verdict_counts": dict(Counter(verdicts)),
                "ok": ok,
                "trials": trials,
            }
            by_mode[m].append(
                {"id": c["id"], "ok": ok, "expect_pass": c["expect_pass"], "verdict": maj}
            )
            print(f"  -> {maj} ok={ok}", flush=True)
        matrix["rows"].append(row)

    summary: dict[str, Any] = {}
    for m, rows in by_mode.items():
        false_pass = [r["id"] for r in rows if (not r["expect_pass"]) and r["verdict"] == "pass"]
        false_fail = [r["id"] for r in rows if r["expect_pass"] and r["verdict"] != "pass"]
        summary[m] = {
            "n": len(rows),
            "ok": sum(1 for r in rows if r["ok"]),
            "false_pass": false_pass,
            "false_fail": false_fail,
        }
    matrix["summary"] = summary
    return matrix


def build_root_cause_trace(cases: list[dict], stability: dict) -> dict:
    anchors = [c for c in cases if c["id"] in {"P39J-05", "P39J-06", "P39J-07"}]
    traces: dict[str, Any] = {}
    for c in anchors:
        facts = structural_facts(c)
        unsupported: list[dict] = []
        if c["id"] in {"P39J-06", "P39J-07"}:
            unsupported = [
                {
                    "verifier_inference": "Claimed final schema collapsed to a single total_* metric",
                    "live_evidence_quote": c.get("live_verifier_evidence"),
                    "contradicted_by_observed_fact": {
                        "final_schema": facts["final_schema"],
                        "result_columns_observed": facts["result_columns_observed"],
                        "aggregate_op_count": facts["aggregate_op_count"],
                        "identical_signature_metric_groups": facts[
                            "identical_evidence_signature_column_sets"
                        ],
                    },
                },
                {
                    "verifier_inference": "Invented aggregation/collapse step not in plan_structure",
                    "contradicted_by_observed_fact": {"ops_sequence": facts["ops_sequence"]},
                },
            ]
        traces[c["id"]] = {
            "manual_judgment": {
                "shadow_correct": c.get("manual_shadow_correct"),
                "expected_offline": "pass" if c["expect_pass"] else "non-pass",
            },
            "observed_fact": facts,
            "planner_claim_excerpt": {
                "reason": (c.get("plan") or {}).get("reason"),
                "final_output_requirements": (c.get("plan") or {}).get(
                    "final_output_requirements"
                ),
            },
            "live_verifier_inference": {
                "verdict": c.get("live_verifier_verdict"),
                "reason_code": c.get("live_verifier_reason"),
                "evidence": c.get("live_verifier_evidence"),
            },
            "stability_v22": (stability.get("cases") or {}).get(c["id"]),
            "unsupported_verifier_assertions": unsupported,
            "evidence_missing_from_v22": [],
            "provisional_root_cause_class": (
                "A_sufficient_evidence_but_unstable_or_hallucinated_reasoning"
                if c["id"] in {"P39J-06", "P39J-07"}
                else "comparator_pass"
            ),
        }
    return {
        "phase": "39K",
        "question": "Why wrong_output_grain on valid rename+join?",
        "classification": "A_sufficient_but_reasoning_unstable_or_hallucinated",
        "smallest_relevant_difference_p05_vs_ff": {
            "finding": (
                "No material V2.2 structural difference: all three are rename+rename+join "
                "with two independently originated metric columns and no aggregate. "
                "Difference is verifier stochastic/hallucinated collapse claim, not plan family."
            ),
            "compare_ops": {cid: traces[cid]["observed_fact"]["ops_sequence"] for cid in traces},
            "compare_final_schema": {
                cid: traces[cid]["observed_fact"]["final_schema"] for cid in traces
            },
        },
        "cases": traces,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stability-n", type=int, default=5)
    ap.add_argument("--ablation-n", type=int, default=1)
    ap.add_argument("--skip-ablation", action="store_true")
    ap.add_argument("--skip-stability", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    freeze = write_baseline_freeze()
    print("baseline freeze", freeze.get("git_head") or freeze.get("git_head"), freeze.get("confirmed_materialization") or freeze.get("confirmed_materialization"), flush=True)

    cases = build_reproduction_cases()
    _dump(
        OUT / "reproduction_cases.json",
        {
            "n": len(cases),
            "cases": [
                {
                    "id": c["id"],
                    "bucket": c["bucket"],
                    "expect_pass": c["expect_pass"],
                    "prompt": c["prompt"],
                    "ops": [s.get("op") for s in ((c.get("plan") or {}).get("steps") or [])],
                }
                for c in cases
            ],
        },
    )

    audit = {c["id"]: structural_facts(c) for c in cases if str(c["id"]).startswith("P39J")}
    _dump(OUT / "structural_audit_p39j.json", audit)

    stability: dict[str, Any] = {"n": 0, "cases": {}}
    if not args.skip_stability:
        stability = run_stability(cases, n=args.stability_n)
        _dump(OUT / "stability_results.json", stability)

    root = build_root_cause_trace(cases, stability)
    _dump(OUT / "root_cause_trace.json", root)

    repro_rows = []
    for c in cases:
        keep = c["id"] in {
            "P39J-05", "P39J-06", "P39J-07", "P39G-11", "P39E-14", "GS1", "FD1"
        } or (c["bucket"] == "c2_collapse" and not c["expect_pass"])
        if not keep or any(r["id"] == c["id"] for r in repro_rows):
            continue
        st = (stability.get("cases") or {}).get(c["id"])
        if st:
            maj = Counter(st["verdict_counts"]).most_common(1)[0][0]
            repro_rows.append(
                {
                    "id": c["id"],
                    "bucket": c["bucket"],
                    "expect_pass": c["expect_pass"],
                    "verdict_majority": maj,
                    "verdict_counts": st["verdict_counts"],
                    "ok": _ok(c["expect_pass"], maj),
                    "source": "stability",
                }
            )
        else:
            r = verify_baseline(c)
            repro_rows.append(
                {
                    "id": c["id"],
                    "bucket": c["bucket"],
                    "expect_pass": c["expect_pass"],
                    "verdict_majority": r["verdict"],
                    "verdict_counts": {r["verdict"]: 1},
                    "ok": _ok(c["expect_pass"], r["verdict"]),
                    "source": "single",
                    "trial": r,
                }
            )
            print(f"[repro] {c['id']} {r['verdict']}", flush=True)
    _dump(OUT / "reproduction_results.json", {"rows": repro_rows})

    ablation: dict[str, Any] = {}
    if not args.skip_ablation:
        ablation = run_ablation_matrix(cases, n=args.ablation_n)
        _dump(OUT / "ablation_matrix.json", ablation)

    try:
        p = subprocess.run(
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "pytest",
                "tests/test_phase39h_provenance_independence.py",
                "tests/test_phase34_generalization.py",
                "-q",
                "--tb=line",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        reg = {
            "exit_code": p.returncode,
            "status": "PASS" if p.returncode == 0 else "FAIL",
            "stdout_tail": p.stdout[-2000:],
            "stderr_tail": p.stderr[-1000:],
        }
    except Exception as exc:  # noqa: BLE001
        reg = {"status": "ERROR", "error": str(exc)}
    _dump(OUT / "regression_results.json", reg)

    gate = "B"
    best = None
    if ablation.get("summary"):
        ranked = []
        for m, s in ablation["summary"].items():
            ranked.append((len(s["false_pass"]), len(s["false_fail"]), -s["ok"], m, s))
        ranked.sort()
        best = {"mode": ranked[0][3], "summary": ranked[0][4]}
        base = ablation["summary"].get("V2.2_baseline", {})
        cand = best["summary"]
        if (
            best["mode"] != "V2.2_baseline"
            and len(cand["false_pass"]) == 0
            and "P39J-06" not in cand["false_fail"]
            and "P39J-07" not in cand["false_fail"]
            and len(cand["false_fail"]) <= len(base.get("false_fail", []))
            and reg.get("status") == "PASS"
        ):
            gate = "A"
        elif best["mode"] != "V2.2_baseline" and len(cand["false_pass"]) > 0:
            gate = "C"
        else:
            gate = "B"

    ff_repro = any(
        ((stability.get("cases") or {}).get(cid) or {}).get("verdict_counts", {}).get("fail", 0) > 0
        for cid in ("P39J-06", "P39J-07")
    )

    summary = {
        "phase": "39K",
        "title": "wrong_output_grain false-fail root cause & evidence ablation",
        "gate": gate,
        "baseline_mode": BASELINE_MODE,
        "root_cause_class": root["classification"],
        "reproduced_live_ff": ff_repro,
        "best_candidate": best,
        "ablation_summary": ablation.get("summary"),
        "stability_key_cases": {
            k: v.get("verdict_counts") for k, v in (stability.get("cases") or {}).items()
        },
        "regression": reg.get("status"),
        "next_recommendation": (
            "limited_shadow_validation"
            if gate == "A"
            else (
                "additional_prompt_or_salience_diagnostics"
                if gate == "B"
                else "reject_candidate_reopen_safety"
            )
        ),
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
    }
    _dump(OUT / "phase39k_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
