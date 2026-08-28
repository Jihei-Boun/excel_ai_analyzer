"""Phase 39N — Exact-payload verifier inference intervention & safety ablation.

Offline research only. Shadow OFF. No production semantic patch.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.semantic_verifier import (
    VERIFIER_SYSTEM_PROMPT,
    build_verifier_payload,
    run_semantic_verification,
    _normalize_verdict,
)
from core.integrate.verifier_invocation_capture import prompt_template_hash
from core.llm_client import _chat_raw, _extract_json_object

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase39n"
CAP_JSONL = (
    ROOT
    / "benchmark_results/multi/phase39m/verifier_captures/verifier_invocations_20260828.jsonl"
)
OBS_PATH = ROOT / "benchmark_results/multi/phase39m/observation_log_reviewed.json"
FIX_H = ROOT / "tests/benchmark_multi/fixtures/phase39h"
FIX_B = ROOT / "tests/benchmark_multi/fixtures/phase39b"
DATA_M = ROOT / "benchmark_results/multi/phase39m/datasets"
NOTE = ROOT / "docs/learning_note/phase39n_verifier_inference_intervention.md"

MODEL = "qwen2.5:7b"
STRONG = "qwen3:32b"
BASELINE_MODE = "final_schema_expr_partition"
PHASE39M_SHA = "185231fbbbbda4b6962f1cd12f2ec870d3a09bf6"
USER_PREFIX_HEAD = "Determine whether the proposed integration plan"

I1_GROUNDING = """
Evidence-grounding discipline (research intervention I1 — CRITICAL):
- Structural claims (aggregation, collapse, total-only output, lost grain, lost side)
  MUST cite concrete observed deterministic evidence from plan_structure and/or
  materialization_evidence (ops, final_schema, origins, evidence_signatures,
  identical_evidence_signature_column_sets).
- planner_claims are NON-AUTHORITATIVE. Do not treat them as proof.
- Do NOT invent fields, aggregations, totals, or missing columns absent from evidence.
- If a collapse / wrong_output_grain claim cannot be supported by observed
  deterministic evidence, do NOT assert that collapse.
- When identical_evidence_signature_column_sets groups claimed side metrics
  together, those aliases are NOT independent sides — fail is appropriate;
  cite that set rather than inventing missing columns.
""".strip()

I3_SELFCHECK = """
Structural self-check before emitting wrong_output_grain (research I3):
Before final verdict, answer internally:
1) What concrete observed operation proves aggregation/collapse?
2) What observed field proves total-only / non-independent output?
3) Which requested grain key disappeared from final_schema (if any)?
4) Do identical_evidence_signature_column_sets / evidence_signatures support
   the claimed collapse or fake dual?
If the collapse claim is unsupported by those observations, do not fabricate it.
If identical signatures show non-independent dual aliases, fail is supported —
cite that evidence rather than inventing missing columns.
""".strip()


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_captures() -> dict[str, dict]:
    by: dict[str, dict] = {}
    for line in CAP_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        by.setdefault(rec["case_id"], rec)
    return by


def extract_payload(cap: dict) -> dict:
    user = cap["exact_verifier_input"]["user"]
    return json.loads(user[user.find("{") :])


def plan_ops(payload: dict) -> list[str]:
    return [s.get("op") for s in (payload.get("plan_structure") or {}).get("steps") or []]


def final_schema_of(payload: dict) -> list:
    me = payload.get("materialization_evidence") or {}
    return list(me.get("final_schema") or [])


def identical_sets_of(payload: dict) -> list:
    me = payload.get("materialization_evidence") or {}
    return list(me.get("identical_evidence_signature_column_sets") or [])


def summarize(trials: list[dict]) -> dict:
    vc = Counter(t["verdict"] for t in trials)
    rc = Counter((t.get("reason_code") or "None") for t in trials)
    n = max(len(trials), 1)
    return {
        "n": len(trials),
        "verdict_counts": dict(vc),
        "reason_counts": dict(rc),
        "pass_rate": vc.get("pass", 0) / n,
        "fail_rate": vc.get("fail", 0) / n,
    }


def replay_exact(
    cap: dict,
    *,
    n: int,
    system: str | None = None,
    user: str | None = None,
    model: str | None = None,
) -> list[dict]:
    exact = cap["exact_verifier_input"]
    sys_p = exact["system"] if system is None else system
    usr = exact["user"] if user is None else user
    model = model or cap.get("model_id") or MODEL
    trials: list[dict] = []
    for i in range(n):
        t0 = time.time()
        raw_text = _chat_raw(
            usr,
            system=sys_p,
            base_url="http://localhost:11434",
            model=model,
            timeout=300,
            format_json=True,
        )
        raw = _extract_json_object(raw_text)
        out = _normalize_verdict(raw)
        trials.append(
            {
                "trial": i + 1,
                "verdict": out.verdict,
                "reason_code": out.reason_code,
                "evidence": list(out.evidence or []),
                "raw_model_response_text": raw_text,
                "latency_s": round(time.time() - t0, 3),
                "model": model,
            }
        )
    return trials


def reformatted_user_i2(cap: dict) -> str:
    payload = extract_payload(cap)
    sections = {
        "USER_INTENT": {"user_prompt": payload.get("user_prompt")},
        "OBSERVED_DETERMINISTIC_STRUCTURE": {
            "plan_structure": payload.get("plan_structure"),
            "materialization_evidence": payload.get("materialization_evidence"),
        },
        "PLANNER_CLAIMS_NON_AUTHORITATIVE": {
            "planner_claims": payload.get("planner_claims"),
            "warning": "Planner claims are not ground truth.",
        },
    }
    for k, v in payload.items():
        if k in {"user_prompt", "plan_structure", "materialization_evidence", "planner_claims"}:
            continue
        sections["OBSERVED_DETERMINISTIC_STRUCTURE"][k] = v
    prefix = cap["exact_verifier_input"]["user"]
    prefix = prefix[: prefix.find("{")]
    return prefix + json.dumps(sections, ensure_ascii=False, indent=2)


def claim_review(case_id: str, trials: list[dict], schema: list | None) -> dict:
    if not trials:
        return {"case_id": case_id, "claim_class": "NO_TRIAL", "note_ko": "시험 없음"}
    t = trials[0]
    text = " ".join(t.get("evidence") or []) + " " + (t.get("raw_model_response_text") or "")
    low = text.lower()
    invented = bool(
        re.search(
            r"single (total|metric)|rather than showing both|only one|collapsed into one|side-by-side",
            low,
        )
    )
    cites = "identical" in low or "same expression" in low or "same aggregate" in low
    schema_str = ", ".join(schema or [])
    if t["verdict"] == "fail" and invented and schema_str:
        klass = "UNSUPPORTED_STRUCTURAL_CLAIM"
        note = (
            f"판정 FAIL은 identical-signature fake dual로 정당할 수 있으나, "
            f"증거 문구가 final_schema({schema_str})에 존재하는 양쪽 컬럼이 "
            f"사라졌거나 side-by-side가 아니라고 서술하면 구조 주장 과잉/부정확."
        )
    elif t["verdict"] == "fail" and cites:
        klass = "SUPPORTED_STRUCTURAL_CLAIM"
        note = "identical/동일 시그니처 근거를 인용한 FAIL — 구조적으로 지지됨."
    elif t["verdict"] == "pass":
        klass = "OTHER_REASONING_ERROR"
        note = "exact capture는 fake dual인데 PASS — false-pass 위험 (R1)."
    else:
        klass = "AMBIGUOUS"
        note = "주장 문구가 시그니처 근거와 컬럼 존재 서술 사이에서 모호함."
    return {
        "case_id": case_id,
        "verdict": t["verdict"],
        "reason_code": t.get("reason_code"),
        "evidence": t.get("evidence"),
        "claim_class": klass,
        "note_ko": note,
    }


def run_fixture(case: dict, model: str = MODEL) -> dict:
    t0 = time.time()
    r = run_semantic_verification(
        user_prompt=case["prompt"],
        plan=case["plan"],
        result=None,
        variant="V2",
        model=model,
        independent=True,
        source_schemas=case.get("source_schemas"),
        materialization_mode=BASELINE_MODE,
    )
    return {
        "id": case["id"],
        "verdict": r.verdict,
        "reason_code": r.reason_code,
        "evidence": list(r.evidence or []),
        "elapsed_s": round(time.time() - t0, 3),
        "model": model,
    }


def fixture_with_system(case: dict, system: str, *, i2: bool = False) -> dict:
    payload = build_verifier_payload(
        user_prompt=case["prompt"],
        plan=case["plan"],
        result=None,
        variant="V2",
        independent=True,
        source_schemas=case.get("source_schemas"),
        materialization_mode=BASELINE_MODE,
    )
    prefix = (
        "Determine whether the proposed integration plan "
        "directly satisfy all material requirements in the user's request.\n"
        "Step order (mandatory):\n"
        "  (1) Reconstruct material requirements from user_prompt only.\n"
        "  (2) Decide from plan_structure + materialization_evidence "
        "whether those requirements are actually materialized.\n"
        "  (3) Optionally glance at planner_claims — never as proof.\n\n"
    )
    if i2:
        body: Any = {
            "USER_INTENT": {"user_prompt": payload.get("user_prompt")},
            "OBSERVED_DETERMINISTIC_STRUCTURE": {
                "plan_structure": payload.get("plan_structure"),
                "materialization_evidence": payload.get("materialization_evidence"),
            },
            "PLANNER_CLAIMS_NON_AUTHORITATIVE": {
                "planner_claims": payload.get("planner_claims"),
            },
        }
    else:
        body = payload
    t0 = time.time()
    raw_text = _chat_raw(
        prefix + json.dumps(body, ensure_ascii=False, indent=2),
        system=system,
        base_url="http://localhost:11434",
        model=MODEL,
        timeout=300,
        format_json=True,
    )
    raw = _extract_json_object(raw_text)
    out = _normalize_verdict(raw)
    return {
        "id": case["id"],
        "verdict": out.verdict,
        "reason_code": out.reason_code,
        "evidence": list(out.evidence or []),
        "elapsed_s": round(time.time() - t0, 3),
        "model": MODEL,
        "raw_model_response_text": raw_text,
    }


def write_freeze() -> dict:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True)
    ver = ROOT / "core/integrate/semantic_verifier.py"
    capmod = ROOT / "core/integrate/verifier_invocation_capture.py"
    freeze = {
        "phase": "39N",
        "frozen_at_utc": utcnow(),
        "git_head": head,
        "phase39m_sha": PHASE39M_SHA,
        "phase39m_sha_matches_head": head == PHASE39M_SHA,
        "dirty_working_tree": bool(dirty.strip()),
        "dirty_paths_sample": [ln for ln in dirty.splitlines() if ln.strip()][:40],
        "official_state": {
            "legacy_primary": True,
            "candidate_research_shadow_only": True,
            "migration": "NOT_APPROVED",
            "shadow_default": "OFF",
            "output_roles": "R-ROLE-B_optional_non_authoritative",
            "verifier_baseline": BASELINE_MODE,
            "verifier_baseline_label": "V2.2",
            "verifier_model": MODEL,
            "temperature": 0,
            "format_json": True,
            "timeout_s": 300,
            "no_semantic_production_changes": True,
        },
        "config_hashes": {
            "semantic_verifier_py_sha256_16": hashlib.sha256(ver.read_bytes()).hexdigest()[:16],
            "verifier_invocation_capture_py_sha256_16": hashlib.sha256(
                capmod.read_bytes()
            ).hexdigest()[:16],
            "prompt_version_hash_16": prompt_template_hash(
                VERIFIER_SYSTEM_PROMPT, USER_PREFIX_HEAD
            )[:16],
            "verifier_system_prompt_sha256_16": hashlib.sha256(
                VERIFIER_SYSTEM_PROMPT.encode()
            ).hexdigest()[:16],
            "verifier_system_prompt_len": len(VERIFIER_SYSTEM_PROMPT),
        },
        "entry_condition": {
            "phase39m_complete": True,
            "phase39m_gate": "C",
            "phase39m_committed": True,
            "working_tree_clean_at_entry": not bool(dirty.strip()),
        },
        "note": "Offline intervention research freeze. Shadow OFF. No production patch.",
    }
    dump(OUT / "baseline_freeze.json", freeze)
    return freeze


def build_suite(captures: dict[str, dict]) -> dict:
    obs_rows = {r["case_id"]: r for r in load(OBS_PATH)["rows"]}

    def exact(cid: str, family: str, exp39m: str, exp_corr: str, notes: str) -> dict:
        cap = captures[cid]
        payload = extract_payload(cap)
        obs = obs_rows.get(cid) or {}
        return {
            "id": cid,
            "source": "EXACT_CAPTURE",
            "family": family,
            "expected_verdict_phase39m_label": exp39m,
            "expected_verdict_evidence_corrected": exp_corr,
            "exact_payload_hash": cap.get("exact_payload_hash"),
            "prompt_version_hash": cap.get("prompt_version_hash"),
            "materialization_version": cap.get("materialization_version"),
            "live_parsed_verdict": cap.get("parsed_verdict"),
            "live_parsed_reason_code": cap.get("parsed_reason_code"),
            "captured_plan_ops": plan_ops(payload),
            "observation_final_plan_ops": [
                s.get("op") for s in ((obs.get("final_plan") or {}).get("steps") or [])
            ],
            "final_schema": final_schema_of(payload),
            "identical_evidence_signature_column_sets": identical_sets_of(payload),
            "shadow_correct": obs.get("shadow_correct"),
            "verifier_false_fail_label": obs.get("verifier_false_fail"),
            "notes": notes,
        }

    suite: dict[str, Any] = {
        "phase": "39N",
        "created_at_utc": utcnow(),
        "families": {
            "A_valid_rename_join_exact_pass": [
                exact(
                    cid,
                    "A_valid_rename_join",
                    "PASS",
                    "PASS",
                    "Exact capture; rename/join; independent signatures.",
                )
                for cid in ["P39M-04", "P39M-05", "P39M-09", "P39M-10"]
            ],
            "A2_misattr_fake_dual_exact_capture": [
                exact(
                    "P39M-07",
                    "D_fake_dual_misattributed_as_valid_ff",
                    "PASS",
                    "NON-PASS",
                    (
                        "Phase 39M labeled verifier FF / valid rename+join. "
                        "Exact capture is union_rows+aggregate with identical "
                        "evidence signatures (P39G-11 isomorphic). Observation "
                        "final_plan is rename+join — capture is rejected fast "
                        "attempt, not final plan."
                    ),
                ),
                exact(
                    "P39M-08",
                    "D_fake_dual_misattributed_as_valid_ff",
                    "PASS",
                    "NON-PASS",
                    "Same misattribution as P39M-07.",
                ),
            ],
            "B_valid_same_origin": [],
            "C_genuine_collapse": [],
            "D_fake_dual_controls": [],
            "E_reconstructed_valid_final_plan": [],
        },
        "reclassification": {
            "P39M-07": "MISATTRIBUTED_FAKE_DUAL_CAPTURE_NOT_FINAL_PLAN",
            "P39M-08": "MISATTRIBUTED_FAKE_DUAL_CAPTURE_NOT_FINAL_PLAN",
            "implication": (
                "Treating exact P39M-07/08 as PASS oracles would reward false-pass "
                "on fake-dual evidence (R1)."
            ),
        },
    }

    p11 = load(FIX_H / "p39g11_canonical.json")
    suite["families"]["D_fake_dual_controls"].append(
        {
            "id": p11["id"],
            "source": "FIXTURE",
            "family": "D_fake_dual",
            "expected_verdict_evidence_corrected": "NON-PASS",
            "prompt": p11["prompt"],
            "plan": p11["plan"],
            "source_schemas": p11.get("source_schemas"),
        }
    )
    for c in load(FIX_H / "fake_dual_family.json")["cases"][:2]:
        suite["families"]["D_fake_dual_controls"].append(
            {
                "id": c["id"],
                "source": "FIXTURE",
                "family": "D_fake_dual",
                "expected_verdict_evidence_corrected": "NON-PASS",
                "prompt": c["prompt"],
                "plan": c["plan"],
                "source_schemas": c.get("source_schemas"),
            }
        )
    for c in load(FIX_H / "genuine_same_origin_dual.json")["cases"][:2]:
        suite["families"]["B_valid_same_origin"].append(
            {
                "id": c["id"],
                "source": "FIXTURE",
                "family": "B_valid_same_origin",
                "expected_verdict_evidence_corrected": "PASS",
                "prompt": c["prompt"],
                "plan": c["plan"],
                "source_schemas": c.get("source_schemas"),
            }
        )
    for c in (load(FIX_B / "c2_and_controls.json").get("cases") or [])[:3]:
        suite["families"]["C_genuine_collapse"].append(
            {
                "id": c.get("id") or "C2",
                "source": "FIXTURE_MAY_LACK_SCHEMAS",
                "family": "C_genuine_collapse",
                "expected_verdict_evidence_corrected": "NON-PASS",
                "prompt": c["prompt"],
                "plan": c["plan"],
                "source_schemas": c.get("source_schemas"),
            }
        )

    for cid in ["P39M-07", "P39M-08"]:
        obs = obs_rows[cid]
        files = obs.get("files") or []
        schemas = {
            f: [str(x) for x in pd.read_excel(DATA_M / cid / f).columns] for f in files
        }
        suite["families"]["E_reconstructed_valid_final_plan"].append(
            {
                "id": f"{cid}-FINAL-PLAN-RECONSTRUCTED",
                "source": "RECONSTRUCTED_FROM_OBSERVATION_FINAL_PLAN",
                "family": "A_valid_rename_join",
                "expected_verdict_evidence_corrected": "PASS",
                "prompt": obs["prompt"],
                "plan": obs["final_plan"],
                "source_schemas": schemas,
                "notes": "Observation final_plan (rename+join), NOT exact capture.",
            }
        )

    dump(OUT / "oracle_suite.json", suite)
    return suite


def write_diff(captures: dict[str, dict], obs_rows: dict[str, dict]) -> dict:
    def summarize_case(cid: str) -> dict:
        cap = captures[cid]
        payload = extract_payload(cap)
        obs = obs_rows.get(cid) or {}
        return {
            "case_id": cid,
            "live_verdict": cap.get("parsed_verdict"),
            "live_reason": cap.get("parsed_reason_code"),
            "user_prompt": payload.get("user_prompt"),
            "captured_plan_ops": plan_ops(payload),
            "final_schema": final_schema_of(payload),
            "identical_sets": identical_sets_of(payload),
            "observation_final_plan_ops": [
                s.get("op") for s in ((obs.get("final_plan") or {}).get("steps") or [])
            ],
            "shadow_correct": obs.get("shadow_correct"),
            "verifier_false_fail_label": obs.get("verifier_false_fail"),
        }

    analysis = {
        "phase": "39N",
        "smallest_correlated_differences": [
            {
                "diff": "captured_plan_ops",
                "PASS_controls": "rename_columns (+join)",
                "FAIL_captures_P39M_07_08": "union_rows then aggregate with two aliases of same sum",
            },
            {
                "diff": "identical_evidence_signature_column_sets",
                "PASS_controls": "empty []",
                "FAIL_captures_P39M_07_08": "groups the two side metric aliases together",
            },
            {
                "diff": "observation_final_plan vs capture",
                "PASS_controls": "aligned",
                "FAIL_captures_P39M_07_08": "MISALIGNED — final_plan rename+join; capture union+agg",
            },
            {
                "diff": "result vs fake-dual simulation",
                "PASS_controls": "independent side values",
                "FAIL_captures_P39M_07_08": "result hash matches correct join, not equal fake-dual totals",
            },
        ],
        "pass_controls": [
            summarize_case(c) for c in ["P39M-04", "P39M-05", "P39M-09", "P39M-10"]
        ],
        "fail_captures": [summarize_case(c) for c in ["P39M-07", "P39M-08"]],
        "root_cause_hypothesis": (
            "Phase 39M false-fail labels for P39M-07/08 are misattributions: "
            "exact verifier payloads are fake-dual plans correctly failed under V2.2; "
            "manual YES used final executed rename+join result columns."
        ),
        "rq3_class": "F_Mixed__primary_misattribution_plus_claim_wording",
        "rq3_detail": {
            "evidence_sufficient_for_FAIL": True,
            "identical_signature_sets_present": True,
            "unsupported_is_claim_wording_not_verdict": True,
            "missing_deterministic_evidence": False,
            "planner_claim_contamination_primary": False,
        },
    }
    dump(OUT / "payload_difference_analysis.json", analysis)
    return analysis


def write_intervention_defs() -> None:
    dump(
        OUT / "intervention_definitions.json",
        {
            "phase": "39N",
            "interventions": [
                {
                    "id": "I0",
                    "name": "Baseline",
                    "change": "Frozen V2.2 + exact user",
                    "architecture_impact": "none",
                },
                {
                    "id": "I1",
                    "name": "Evidence-grounding instruction",
                    "change": "Append grounding to system; user unchanged",
                    "architecture_impact": "prompt-only",
                },
                {
                    "id": "I2",
                    "name": "Claim/evidence section separation",
                    "change": "Reformat user into USER_INTENT / OBSERVED / PLANNER_CLAIMS",
                    "architecture_impact": "presentation-only",
                },
                {
                    "id": "I3",
                    "name": "Structural self-check",
                    "change": "I1 + self-check before wrong_output_grain",
                    "architecture_impact": "prompt-only",
                },
                {
                    "id": "I4",
                    "name": "New deterministic evidence",
                    "change": "SKIPPED — identical sets already present",
                    "architecture_impact": "none_skipped",
                },
                {
                    "id": "I5",
                    "name": "Two-pass verifier",
                    "change": "SKIPPED — unjustified for correcting a correct FAIL",
                    "architecture_impact": "none_skipped",
                },
            ],
        },
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline-n", type=int, default=5)
    ap.add_argument("--stability-n", type=int, default=10)
    ap.add_argument("--intervention-n", type=int, default=5)
    ap.add_argument("--skip-interventions", action="store_true")
    ap.add_argument("--skip-stability", action="store_true")
    ap.add_argument("--skip-model-comparison", action="store_true")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    if args.quick:
        args.baseline_n = 2
        args.stability_n = 2
        args.intervention_n = 2

    OUT.mkdir(parents=True, exist_ok=True)
    print("[39N] freeze", flush=True)
    freeze = write_freeze()

    captures = load_captures()
    obs_rows = {r["case_id"]: r for r in load(OBS_PATH)["rows"]}
    print("[39N] oracle suite", flush=True)
    suite = build_suite(captures)
    print("[39N] payload diff", flush=True)
    diff = write_diff(captures, obs_rows)
    write_intervention_defs()

    baseline_rep: dict[str, Any] = {
        "phase": "39N",
        "n_ff_anchors": args.baseline_n,
        "cases": {},
        "stop_baseline_drift": False,
    }
    for cid in ["P39M-07", "P39M-08"]:
        print(f"[39N] baseline {cid} n={args.baseline_n}", flush=True)
        trials = replay_exact(captures[cid], n=args.baseline_n)
        summary = summarize(trials)
        baseline_rep["cases"][cid] = {
            "exact_payload_hash": captures[cid].get("exact_payload_hash"),
            "summary": summary,
            "trials": trials,
            "expected_live": "fail",
        }
        if summary["verdict_counts"].get("fail", 0) < args.baseline_n:
            baseline_rep["stop_baseline_drift"] = True
            baseline_rep["drift_case"] = cid
    for cid in ["P39M-04", "P39M-05", "P39M-09", "P39M-10"]:
        print(f"[39N] baseline {cid} n=1", flush=True)
        trials = replay_exact(captures[cid], n=1)
        baseline_rep["cases"][cid] = {
            "exact_payload_hash": captures[cid].get("exact_payload_hash"),
            "summary": summarize(trials),
            "trials": trials,
        }
    dump(OUT / "baseline_reproduction.json", baseline_rep)
    if baseline_rep["stop_baseline_drift"]:
        dump(
            OUT / "phase39n_summary.json",
            {
                "phase": "39N",
                "gate": "STOP-BASELINE-DRIFT",
                "baseline_reproduction": baseline_rep,
            },
        )
        raise SystemExit("STOP-BASELINE-DRIFT")

    ablation_rows: list[dict] = []
    reviews: list[dict] = []
    stability: dict[str, Any] = {"phase": "39N", "cases": {}}

    def add_row(
        interv: str,
        case_id: str,
        family: str,
        expected: str,
        summary: dict,
        trials: list[dict],
        schema: list | None = None,
    ) -> None:
        ablation_rows.append(
            {
                "intervention": interv,
                "case_id": case_id,
                "family": family,
                "expected_corrected": expected,
                "verdict_mode": (
                    max(summary["verdict_counts"], key=summary["verdict_counts"].get)
                    if summary["verdict_counts"]
                    else None
                ),
                "pass_rate": summary["pass_rate"],
                "fail_rate": summary["fail_rate"],
                "n": summary["n"],
                "verdict_counts": summary["verdict_counts"],
                "reason_counts": summary["reason_counts"],
            }
        )
        reviews.append(
            claim_review(f"{case_id}/{interv}", trials, schema)
            | {"intervention": interv, "base_case_id": case_id}
        )

    for cid in ["P39M-07", "P39M-08"]:
        add_row(
            "I0",
            cid,
            "misattr_fake_dual",
            "NON-PASS",
            baseline_rep["cases"][cid]["summary"],
            baseline_rep["cases"][cid]["trials"],
            final_schema_of(extract_payload(captures[cid])),
        )
    for cid in ["P39M-04", "P39M-05"]:
        add_row(
            "I0",
            cid,
            "valid_rename_join",
            "PASS",
            baseline_rep["cases"][cid]["summary"],
            baseline_rep["cases"][cid]["trials"],
            final_schema_of(extract_payload(captures[cid])),
        )

    fixture_cases: list[dict] = []
    for key in [
        "D_fake_dual_controls",
        "B_valid_same_origin",
        "C_genuine_collapse",
        "E_reconstructed_valid_final_plan",
    ]:
        fixture_cases.extend(suite["families"][key])

    fixture_baseline = []
    for fc in fixture_cases:
        print(f"[39N] fixture I0 {fc['id']}", flush=True)
        fr = run_fixture(fc)
        fixture_baseline.append(fr)
        ablation_rows.append(
            {
                "intervention": "I0",
                "case_id": fc["id"],
                "family": fc["family"],
                "expected_corrected": fc["expected_verdict_evidence_corrected"],
                "verdict_mode": fr["verdict"],
                "pass_rate": 1.0 if fr["verdict"] == "pass" else 0.0,
                "fail_rate": 1.0 if fr["verdict"] == "fail" else 0.0,
                "n": 1,
                "verdict_counts": {fr["verdict"]: 1},
                "reason_counts": {fr.get("reason_code") or "None": 1},
            }
        )

    safety_ids: set[str] = set()
    for fam_key, n in [
        ("D_fake_dual_controls", 2),
        ("B_valid_same_origin", 1),
        ("E_reconstructed_valid_final_plan", 1),
    ]:
        for fc in suite["families"][fam_key][:n]:
            safety_ids.add(fc["id"])

    if not args.skip_interventions:
        configs = [
            ("I1", VERIFIER_SYSTEM_PROMPT + "\n\n" + I1_GROUNDING, False),
            ("I2", VERIFIER_SYSTEM_PROMPT, True),
            (
                "I3",
                VERIFIER_SYSTEM_PROMPT + "\n\n" + I1_GROUNDING + "\n\n" + I3_SELFCHECK,
                False,
            ),
        ]
        for iid, sys_p, is_i2 in configs:
            for cid in ["P39M-07", "P39M-08", "P39M-04"]:
                print(f"[39N] {iid} {cid} n={args.intervention_n}", flush=True)
                user = reformatted_user_i2(captures[cid]) if is_i2 else None
                trials = replay_exact(
                    captures[cid], n=args.intervention_n, system=sys_p, user=user
                )
                summary = summarize(trials)
                fam = (
                    "misattr_fake_dual"
                    if cid in {"P39M-07", "P39M-08"}
                    else "valid_rename_join"
                )
                exp = "NON-PASS" if cid in {"P39M-07", "P39M-08"} else "PASS"
                add_row(
                    iid,
                    cid,
                    fam,
                    exp,
                    summary,
                    trials,
                    final_schema_of(extract_payload(captures[cid])),
                )
            for fc in fixture_cases:
                if fc["id"] not in safety_ids:
                    continue
                print(f"[39N] {iid} fixture {fc['id']}", flush=True)
                fr = fixture_with_system(fc, sys_p, i2=is_i2)
                trials = [
                    {
                        "verdict": fr["verdict"],
                        "reason_code": fr["reason_code"],
                        "evidence": fr["evidence"],
                        "raw_model_response_text": fr.get("raw_model_response_text"),
                    }
                ]
                summary = summarize(trials)
                ablation_rows.append(
                    {
                        "intervention": iid,
                        "case_id": fc["id"],
                        "family": fc["family"],
                        "expected_corrected": fc["expected_verdict_evidence_corrected"],
                        "verdict_mode": fr["verdict"],
                        "pass_rate": summary["pass_rate"],
                        "fail_rate": summary["fail_rate"],
                        "n": 1,
                        "verdict_counts": summary["verdict_counts"],
                        "reason_counts": summary["reason_counts"],
                    }
                )

    if not args.skip_stability:
        for cid in ["P39M-07", "P39M-08"]:
            print(f"[39N] stability I0 {cid} n={args.stability_n}", flush=True)
            trials = replay_exact(captures[cid], n=args.stability_n)
            stability["cases"][cid] = {
                "intervention": "I0",
                "summary": summarize(trials),
                "trials": [
                    {k: v for k, v in t.items() if k != "raw_model_response_text"}
                    for t in trials
                ],
            }

    model_cmp: dict[str, Any] = {"phase": "39N", "performed": False, "cases": {}}
    if not args.skip_model_comparison:
        model_cmp["performed"] = True
        for cid in ["P39M-07", "P39M-08"]:
            print(f"[39N] 32B {cid}", flush=True)
            trials = replay_exact(
                captures[cid], n=min(3, args.intervention_n), model=STRONG
            )
            model_cmp["cases"][cid] = summarize(trials)
        p11 = next(c for c in fixture_cases if c["id"] == "P39G-11")
        print("[39N] 32B P39G-11", flush=True)
        fr = run_fixture(p11, model=STRONG)
        model_cmp["cases"]["P39G-11"] = {
            "verdict_counts": {fr["verdict"]: 1},
            "reason_counts": {fr.get("reason_code") or "None": 1},
            "pass_rate": 1.0 if fr["verdict"] == "pass" else 0.0,
        }

    def false_pass(interv: str) -> list[str]:
        return [
            r["case_id"]
            for r in ablation_rows
            if r["intervention"] == interv
            and r["expected_corrected"] == "NON-PASS"
            and r.get("pass_rate", 0) > 0
        ]

    def false_fail(interv: str) -> list[str]:
        return [
            r["case_id"]
            for r in ablation_rows
            if r["intervention"] == interv
            and r["expected_corrected"] == "PASS"
            and r.get("pass_rate", 0) < 0.5
        ]

    safety: dict[str, Any] = {}
    for iid in ["I0", "I1", "I2", "I3"]:
        fp = false_pass(iid)
        ff = false_fail(iid)
        safety[iid] = {
            "false_passes": fp,
            "false_fails_on_valid": ff,
            "r1_fake_dual_false_pass": any(
                x.startswith("P39G-11")
                or x.startswith("FD")
                or x in {"P39M-07", "P39M-08"}
                for x in fp
            ),
            "viable_for_gate_a": False,
        }

    i1_keeps_fail = all(
        next(
            (
                r
                for r in ablation_rows
                if r["intervention"] == "I1" and r["case_id"] == cid
            ),
            {"fail_rate": 0.0},
        ).get("fail_rate", 0.0)
        >= 0.8
        for cid in ["P39M-07", "P39M-08"]
    )
    gate = "C"
    gate_reason = (
        "Exact P39M-07/08 captures are misattributed fake-dual payloads (correct FAIL). "
        "Making them PASS would violate R1. No production intervention adopted."
    )
    if i1_keeps_fail and not safety.get("I1", {}).get("r1_fake_dual_false_pass"):
        gate = "B"
        gate_reason = (
            "Root cause substantially clarified (misattribution + claim wording). "
            "Prompt interventions are not a Gate-A production candidate because the "
            "'FF oracles' should remain NON-PASS. Optional claim-quality prompt tweaks "
            "remain research-only; Shadow validation of a 'fix' is not justified."
        )

    dump(
        OUT / "ablation_results.json",
        {
            "phase": "39N",
            "rows": ablation_rows,
            "safety_by_intervention": safety,
            "matrix_note": (
                "Expected column uses evidence-corrected labels. Phase 39M PASS label "
                "for P39M-07/08 is superseded."
            ),
        },
    )
    with (OUT / "ablation_results.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "intervention",
            "case_id",
            "family",
            "expected_corrected",
            "verdict_mode",
            "pass_rate",
            "fail_rate",
            "n",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in ablation_rows:
            w.writerow({k: r.get(k) for k in fields})

    dump(OUT / "stability_results.json", stability)
    dump(
        OUT / "manual_claim_review.json",
        {
            "phase": "39N",
            "reviews": reviews,
            "disclaimer": "연구/수동 라벨. Python이 환각을 자동 판정하지 않음.",
        },
    )
    dump(
        OUT / "regression_results.json",
        {
            "phase": "39N",
            "note": "Compact offline regression; production semantics unchanged",
            "fixture_baseline": fixture_baseline,
            "suites_covered": [
                "phase39m_exact_ff_oracles",
                "phase39m_exact_pass_controls",
                "phase39h_p39g11_fd_gs",
                "phase39b_c2_sample",
                "phase39m_final_plan_reconstructed",
            ],
        },
    )
    dump(OUT / "model_comparison.json", model_cmp)

    summary = {
        "phase": "39N",
        "title": "Exact-Payload Verifier Inference Intervention & Safety Ablation",
        "gate": gate,
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
        "baseline_reproduction": {
            cid: baseline_rep["cases"][cid]["summary"] for cid in ["P39M-07", "P39M-08"]
        },
        "root_cause_classification": diff["rq3_class"],
        "root_cause_detail": diff["rq3_detail"],
        "best_intervention": "NONE_FOR_PRODUCTION",
        "best_intervention_note": (
            "Do not adopt an intervention that converts P39M-07/08 exact captures to PASS. "
            "Optional I1/I3 may improve claim citation quality while preserving FAIL."
        ),
        "p39m_07_08_result": "STABLE_FAIL_CORRECT_UNDER_EVIDENCE",
        "wrong_control_safety": safety,
        "gate_reason": gate_reason,
        "phase39m_sha": PHASE39M_SHA,
        "git_head": freeze["git_head"],
        "next_recommendation": (
            "Do not run Shadow validation of a P39M-07/08 'fix'. "
            "Next: (1) improve capture attribution (which attempt/plan was verified), "
            "(2) optionally research claim-quality prompts that cite identical signatures "
            "without weakening fake-dual rejection, (3) treat reconstructed final-plan "
            "rename+join as the valid PASS comparator — not the exact FF captures."
        ),
    }
    dump(OUT / "phase39n_summary.json", summary)

    NOTE.parent.mkdir(parents=True, exist_ok=True)
    NOTE.write_text(
        f"""# Phase 39N — Verifier Inference Intervention Learning Note

## Entry
- Phase 39M SHA: `{PHASE39M_SHA}` (Gate C)
- HEAD at freeze: `{freeze['git_head']}`
- Shadow: OFF · Migration: NOT_APPROVED

## Critical finding
P39M-07/08 exact captured verifier payloads are **not** valid rename+join plans.

| Artifact | P39M-07/08 |
|---|---|
| Exact capture `plan_structure` | `union_rows` → `aggregate` with two aliases of the same sum over the same union |
| `identical_evidence_signature_column_sets` | groups the two side metrics |
| Observation `final_plan` | `rename_columns` → `join` |
| Result content | matches **correct join** values, not fake-dual equal totals |

Therefore Phase 39M “verifier false-fail on valid rename+join” was a **misattribution**:
the verifier correctly failed a rejected fast-path fake-dual plan; escalation later produced the good rename+join result that manual review scored YES.

These captures are isomorphic to **P39G-11** fake dual. Expected corrected label: **NON-PASS**.

## RQ answers (short)
1. V2.2 “hallucination” framing is incomplete — FAIL is evidence-supported; wording may over-claim “columns collapsed away”.
2. PASS vs FAIL captures differ by ops + identical signature sets + capture≠final plan.
3. Class **F (mixed)** — primary misattribution; secondary unsupported claim wording.
4–6. Prompt grounding/self-check may improve wording; must not convert these captures to PASS.

## Interventions
- I0 baseline: stable FAIL on exact captures (no STOP-BASELINE-DRIFT).
- I1–I3: research-only; I4/I5 skipped (evidence not missing; two-pass unjustified).
- Gate **{gate}**: {gate_reason}

## Safety
Any intervention that makes exact P39M-07/08 PASS fails R1 relative to fake-dual semantics.

## Architecture
LLM = semantic decision · Python = deterministic observation. Unchanged. No production patch.
""",
        encoding="utf-8",
    )
    print("[39N] DONE gate=", gate, flush=True)


if __name__ == "__main__":
    main()
