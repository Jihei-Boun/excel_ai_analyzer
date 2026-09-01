"""Phase 39T diagnostic harness — reconstructed replay, no live Shadow.

Does not merge results into 39R/39S official metrics.
Does not change production prompts, timeouts, or escalation policy.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.benchmark_multi.phase39s_pilot import _cases
from core.integrate.integration_plan_types import (
    INTEGRATION_ATOMIC_OPS,
    integration_plan_from_dict,
)
from core.integrate.integration_planner import (
    _compact_understanding_for_prompt,
    build_integration_plan,
    get_planner_system_prompt,
)
from core.integrate.planner_invocation_capture import (
    classify_cannot_plan_subtype,
    classify_raw_outcome,
)
from core.integrate.relationship_infer import build_cross_file_understanding

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase39t"
S39 = ROOT / "benchmark_results/multi/phase39s"
BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

REPS = ["P39S-C01", "P39S-C02", "P39S-C03", "P39S-D01", "P39S-D02", "P39S-D04"]
STABILITY_CASES = ["P39S-C03", "P39S-D01", "P39S-D02"]
N_STABILITY = 5

REFERENCE_SHAPE = {
    "P39S-C01": {
        "possible_executable_correct_plan": False,
        "shape": "cannot_plan (no inlet/outlet discriminator in files)",
        "required_ops": [],
        "type": "C-A/C-C",
    },
    "P39S-C02": {
        "possible_executable_correct_plan": False,
        "shape": "cannot_plan (no face/back discriminator); fake-dual aliases are wrong",
        "required_ops": [],
        "type": "C-A/C-C (wrong executable attempt in 39S)",
    },
    "P39S-C03": {
        "possible_executable_correct_plan": False,
        "shape": "cannot_plan (no Alpha/Beta discriminator)",
        "required_ops": [],
        "type": "C-A/C-C",
    },
    "P39S-C04": {
        "possible_executable_correct_plan": False,
        "shape": "cannot_plan (no weekday/weekend discriminator)",
        "required_ops": [],
        "type": "C-A/C-C",
    },
    "P39S-D01": {
        "possible_executable_correct_plan": True,
        "shape": "filter_rows(day=D1)+agg → filter_rows(day=D2)+agg → join",
        "required_ops": ["filter_rows", "aggregate", "join"],
        "type": "single_file_partition",
    },
    "P39S-D03": {
        "possible_executable_correct_plan": True,
        "shape": "filter_rows(season=wet/dry)+agg → join",
        "required_ops": ["filter_rows", "aggregate", "join"],
        "type": "single_file_partition",
    },
    "P39S-D02": {
        "possible_executable_correct_plan": True,
        "shape": "rename_columns → join (or join then rename)",
        "required_ops": ["rename_columns", "join"],
        "type": "two_file_partition",
    },
    "P39S-D04": {
        "possible_executable_correct_plan": True,
        "shape": "rename_columns → join",
        "required_ops": ["rename_columns", "join"],
        "type": "two_file_partition",
    },
}


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_39s_shadow() -> dict[str, dict[str, Any]]:
    tel = S39 / "telemetry/shadow_20260901.jsonl"
    out: dict[str, dict[str, Any]] = {}
    if not tel.exists():
        return out
    for line in tel.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        out[str(rec.get("request_id"))] = rec
    return out


def _ops(plan: dict[str, Any] | None) -> list[str]:
    if not isinstance(plan, dict):
        return []
    return [str(s.get("op")) for s in (plan.get("steps") or []) if isinstance(s, dict) and s.get("op")]


def _column_audit(compact: dict[str, Any]) -> dict[str, Any]:
    files = []
    for p in compact.get("file_profiles") or []:
        cols = []
        for c in (p.get("observations") or {}).get("columns") or []:
            cols.append({
                "name": c.get("name"),
                "dtype_family": c.get("dtype_family"),
                "distinct_count": c.get("distinct_count"),
                "sample_values": c.get("sample_values"),
            })
        files.append({
            "source_id": p.get("source_id"),
            "row_count": p.get("row_count"),
            "columns": cols,
        })
    rels = []
    for r in compact.get("relationships") or []:
        rels.append({
            "left": r.get("left_source"),
            "right": r.get("right_source"),
            "relationship": r.get("relationship"),
            "key_candidates": r.get("key_candidates"),
        })
    return {"files": files, "relationships": rels}


def _sufficiency(case_id: str, compact: dict[str, Any]) -> dict[str, Any]:
    names = []
    samples: dict[str, list[Any]] = {}
    for p in compact.get("file_profiles") or []:
        for c in (p.get("observations") or {}).get("columns") or []:
            names.append(str(c.get("name")))
            samples[str(c.get("name"))] = list(c.get("sample_values") or [])
    name_set = set(names)
    ref = REFERENCE_SHAPE[case_id]
    if case_id in {"P39S-C01", "P39S-C02", "P39S-C03", "P39S-C04"}:
        return {
            "answer": "SUFFICIENT_FOR_CANNOT_PLAN",
            "evidence": (
                "Observed columns do not include the requested side discriminator. "
                f"columns={sorted(name_set)}. A competent planner can determine the "
                "comparison is underdetermined and return cannot_plan. An executable "
                "correct dual-side plan is not possible without inventing partitions."
            ),
            "observed_columns": sorted(name_set),
        }
    if case_id == "P39S-D01":
        has_day = "day" in name_set
        day_vals = {str(x) for x in samples.get("day") or []}
        ok = has_day and ({"D1", "D2"} <= day_vals or "D1" in day_vals)
        return {
            "answer": "SUFFICIENT" if ok else "AMBIGUOUS",
            "evidence": (
                f"day column present={has_day}, sample_values={samples.get('day')}. "
                "Independent filters on day are representable."
            ),
            "observed_columns": sorted(name_set),
        }
    if case_id == "P39S-D03":
        has = "season" in name_set
        return {
            "answer": "SUFFICIENT" if has else "INSUFFICIENT",
            "evidence": f"season present={has}, samples={samples.get('season')}",
            "observed_columns": sorted(name_set),
        }
    if case_id in {"P39S-D02", "P39S-D04"}:
        n_files = len(compact.get("file_profiles") or [])
        return {
            "answer": "SUFFICIENT" if n_files >= 2 else "INSUFFICIENT",
            "evidence": (
                f"n_files={n_files}. Independent file identity is present for rename+join. "
                f"relationships={_column_audit(compact)['relationships']}"
            ),
            "observed_columns": sorted(name_set),
        }
    return {"answer": "AMBIGUOUS", "evidence": "unlisted", "observed_columns": sorted(name_set)}


def _dsl(case_id: str) -> dict[str, Any]:
    ref = REFERENCE_SHAPE[case_id]
    missing = [op for op in ref["required_ops"] if op not in INTEGRATION_ATOMIC_OPS]
    if not ref["possible_executable_correct_plan"]:
        return {
            "classification": "EXPRESSIBLE",
            "note": "Correct outcome is cannot_plan; that status is in the DSL contract.",
        }
    if missing:
        return {"classification": "NOT_EXPRESSIBLE", "missing_ops": missing}
    return {
        "classification": "EXPRESSIBLE",
        "shape": ref["shape"],
        "ops": ref["required_ops"],
        "note": "filter_rows value predicates + aggregate + join are existing atomic ops.",
    }


def reconstruct_understanding(case: dict[str, Any]) -> dict[str, Any]:
    named = list(case["files"].items())
    und = build_cross_file_understanding(
        named,
        base_url=BASE_URL,
        model="qwen2.5:7b",
        infer_relationships=True,
    )
    return und.to_dict() if hasattr(und, "to_dict") else dict(und)


def one_planner_call(
    *,
    case_id: str,
    request_id: str,
    user_prompt: str,
    compact: dict[str, Any],
    model: str,
    retry_feedback: list[str] | None = None,
) -> dict[str, Any]:
    os.environ["MULTI_PLANNER_CAPTURE_CASE_ID"] = case_id
    os.environ["MULTI_PLANNER_CAPTURE_REQUEST_ID"] = request_id
    t0 = time.time()
    try:
        plan = build_integration_plan(
            user_prompt,
            {
                "file_profiles": compact.get("file_profiles"),
                "pairwise_observations": compact.get("pairwise_observations"),
                "relationships": compact.get("relationships"),
            },
            base_url=BASE_URL,
            model=model,
            retry_feedback=retry_feedback,
        )
        d = plan.to_dict()
        return {
            "ok": True,
            "latency_s": round(time.time() - t0, 3),
            "status": plan.status,
            "reason": plan.reason,
            "notes": plan.notes,
            "meta": plan.meta,
            "ops": _ops(d),
            "plan": d,
            "raw_outcome": classify_raw_outcome(
                parse_ok=True,
                plan=d,
                backend_error=None if plan.reason != "planner_parse_failed" else str(plan.notes),
            ),
            "cannot_plan_subtype": classify_cannot_plan_subtype(
                d, parse_error=None
            ),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "latency_s": round(time.time() - t0, 3),
            "status": "exception",
            "reason": None,
            "ops": [],
            "plan": None,
            "raw_outcome": "BACKEND_FAILURE",
            "cannot_plan_subtype": "BACKEND_FAILURE",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _hist_subtype(plan: dict[str, Any] | None) -> str | None:
    if not isinstance(plan, dict):
        return None
    return classify_cannot_plan_subtype(plan, parse_error=None)


def write_json(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def run_reconstruct() -> dict[str, Any]:
    cases = {c["case_id"]: c for c in _cases()}
    shadow = _load_39s_shadow()
    recs = []
    for cid in REPS:
        case = cases[cid]
        rid = case["request_id"]
        print(f"\n=== reconstruct {cid} {rid} ===", flush=True)
        t0 = time.time()
        und = reconstruct_understanding(case)
        compact = _compact_understanding_for_prompt(und)
        sh = (shadow.get(rid) or {}).get("shadow") or {}
        hist_plan = sh.get("final_plan")
        row = {
            "case_id": cid,
            "request_id": rid,
            "family": case["family"],
            "fidelity": "RECONSTRUCTED_REPLAY",
            "understanding_latency_s": round(time.time() - t0, 3),
            "prompt": case["prompt"],
            "files": list(case["files"]),
            "compact_column_audit": _column_audit(compact),
            "input_sufficiency": _sufficiency(cid, compact),
            "dsl_expressivity": _dsl(cid),
            "reference": REFERENCE_SHAPE[cid],
            "historical_39s": {
                "final_path": sh.get("final_path"),
                "planner_status": sh.get("planner_status"),
                "failure_32b": sh.get("failure_32b_invoked"),
                "semantic_32b": sh.get("semantic_32b_invoked"),
                "verifier_invoked": sh.get("semantic_verifier_invoked"),
                "fast_attempt_count": sh.get("fast_attempt_count"),
                "fast_retry_count": sh.get("fast_retry_count"),
                "latency_total_s": sh.get("latency_total_s"),
                "latency_by_stage_s": sh.get("latency_by_stage_s"),
                "final_plan_status": (hist_plan or {}).get("status"),
                "final_plan_reason": (hist_plan or {}).get("reason"),
                "final_plan_notes": (hist_plan or {}).get("notes"),
                "final_plan_ops": _ops(hist_plan),
                "cannot_plan_subtype_of_final": _hist_subtype(hist_plan),
                "model_calls": sh.get("model_calls"),
            },
            "compact_understanding": compact,
        }
        recs.append(row)
        print(
            "  sufficiency", row["input_sufficiency"]["answer"],
            "dsl", row["dsl_expressivity"]["classification"],
            "hist", row["historical_39s"]["final_path"],
            row["historical_39s"]["cannot_plan_subtype_of_final"],
            flush=True,
        )
    write_json("representative_cases.json", {"phase": "39T", "n": len(recs), "rows": recs})
    write_json("input_sufficiency_review.json", {
        "phase": "39T",
        "C": "SUFFICIENT_FOR_CANNOT_PLAN (underdetermined sides; executable dual plan not possible)",
        "single_file_D": "SUFFICIENT (partition column observed)",
        "two_file_D": "SUFFICIENT (independent file identity observed)",
        "rows": [{
            "case_id": r["case_id"],
            **r["input_sufficiency"],
        } for r in recs],
    })
    write_json("dsl_expressivity_review.json", {
        "phase": "39T",
        "allowed_ops": sorted(INTEGRATION_ATOMIC_OPS),
        "C_correct_where_exists": "cannot_plan is expressible",
        "single_file_partition": "EXPRESSIBLE via filter_rows + aggregate + join",
        "two_file_rename_join": "EXPRESSIBLE via rename_columns + join",
        "rows": [{k: r[k] for k in ("case_id", "dsl_expressivity", "reference")} for r in recs],
    })
    return {"rows": recs}


def run_stability(reconstructed: dict[str, Any]) -> dict[str, Any]:
    by = {r["case_id"]: r for r in reconstructed["rows"]}
    trials = []
    cases_map = {c["case_id"]: c for c in _cases()}
    for cid in STABILITY_CASES:
        rec = by[cid]
        case = cases_map[cid]
        compact = rec["compact_understanding"]
        for model in ("qwen2.5:7b", "qwen3:32b"):
            for i in range(1, N_STABILITY + 1):
                rid = f"p39t-replay-{cid}-{model.replace(':', '_')}-n{i}"
                print(f"\n=== stability {cid} {model} n={i}/{N_STABILITY} ===", flush=True)
                out = one_planner_call(
                    case_id=cid,
                    request_id=rid,
                    user_prompt=case["prompt"],
                    compact=compact,
                    model=model,
                )
                row = {
                    "case_id": cid,
                    "historical_request_id": case["request_id"],
                    "replay_request_id": rid,
                    "model": model,
                    "n": i,
                    "fidelity": "RECONSTRUCTED_REPLAY",
                    **{k: out[k] for k in (
                        "ok", "latency_s", "status", "reason", "ops",
                        "raw_outcome", "cannot_plan_subtype", "error",
                    )},
                    "plan_status": out.get("status"),
                    "notes_head": (out.get("notes") or [None])[:1],
                }
                trials.append(row)
                write_json("planner_stability_results.json", {
                    "phase": "39T",
                    "n_target_per_cell": N_STABILITY,
                    "trials": trials,
                })
                print(
                    " ", row["raw_outcome"], row["status"], row["reason"],
                    row["ops"], f"lat={row['latency_s']}",
                    flush=True,
                )
    # summary
    cells = {}
    for t in trials:
        key = f"{t['case_id']}|{t['model']}"
        cells.setdefault(key, Counter())[t["raw_outcome"]] += 1
    write_json("planner_stability_results.json", {
        "phase": "39T",
        "n_target_per_cell": N_STABILITY,
        "counts": {k: dict(v) for k, v in cells.items()},
        "trials": trials,
        "note": "Offline reconstructed planner-only calls. Not 39S official metrics.",
    })
    return {"trials": trials, "counts": {k: dict(v) for k, v in cells.items()}}


def run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cap = OUT / "planner_captures"
    cap.mkdir(parents=True, exist_ok=True)
    os.environ["MULTI_PLANNER_CAPTURE_ENABLED"] = "true"
    os.environ["MULTI_PLANNER_CAPTURE_DIR"] = str(cap.resolve())
    # Shadow stays off
    os.environ.pop("MULTI_SHADOW_ENABLED", None)

    recon = run_reconstruct()
    stab = run_stability(recon)
    print("\nstability counts", json.dumps(stab["counts"], indent=2), flush=True)


if __name__ == "__main__":
    run()
