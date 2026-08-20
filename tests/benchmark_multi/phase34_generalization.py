"""Phase 34 — 7B V1 semantic verifier generalization & invocation simulation.

Offline only. Freezes Phase 33 verifier (Prompt+Plan / qwen2.5:7b).
Does NOT wire into production pipeline, tune prompts, or add routers.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from core.integrate.semantic_verifier import (
    VERIFIER_SYSTEM_PROMPT,
    build_verifier_payload,
    run_semantic_verification,
)
from tests.benchmark_multi.schema import load_all_cases

OUT = Path("benchmark_results/multi/phase34")

SOURCES = [
    Path("benchmark_results/multi/phase27/qwen2.5_7b/full_19"),
    Path("benchmark_results/multi/phase27/qwen3_32b/full_19"),
    Path("benchmark_results/multi/phase28/live_escalation"),
    Path("benchmark_results/multi/phase30/live_grain_hardening"),
]

FROZEN_MODEL = "qwen2.5:7b"
FROZEN_VARIANT = "V1"
BASELINE_PIPELINE_S = 34.14


def _prompt_fingerprint() -> dict[str, Any]:
    h = hashlib.sha256(VERIFIER_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    return {
        "model": FROZEN_MODEL,
        "variant": FROZEN_VARIANT,
        "context": "user_prompt + IntegrationPlan only",
        "temperature": 0,
        "verifier_system_sha256": h,
        "verifier_system_chars": len(VERIFIER_SYSTEM_PROMPT),
        "phase33_freeze": True,
        "no_v2_v3": True,
    }


def _ops(plan: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(s.get("op")) for s in (plan.get("steps") or []) if isinstance(s, dict))


def _grain(plan: dict[str, Any]) -> str | None:
    req = plan.get("final_output_requirements") or {}
    g = req.get("grain")
    return str(g) if g else None


def _plan_sig(plan: dict[str, Any]) -> str:
    return json.dumps(
        {
            "ops": list(_ops(plan)),
            "grain": _grain(plan),
            "req": (plan.get("final_output_requirements") or {}).get("required_columns"),
            "steps": [
                {
                    "op": s.get("op"),
                    "params": {
                        k: (s.get("params") or {}).get(k)
                        for k in (
                            "group_by",
                            "metrics",
                            "left_keys",
                            "right_keys",
                            "columns",
                            "column_policy",
                            "mapping",
                        )
                        if (s.get("params") or {}).get(k) is not None
                    },
                }
                for s in (plan.get("steps") or [])
                if isinstance(s, dict)
            ],
        },
        sort_keys=True,
    )


def _harvest_historical() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases = {c.id: c for c in load_all_cases()}
    valid: list[dict[str, Any]] = []
    type_c: list[dict[str, Any]] = []
    seen_valid: set[str] = set()
    seen_c: set[str] = set()

    for root in SOURCES:
        if not root.exists():
            continue
        for path in sorted(root.glob("2026*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for c in data.get("cases") or []:
                if c.get("status") != "success" or not c.get("plan"):
                    continue
                if c.get("unsafe_execution"):
                    continue
                cid = str(c.get("case_id"))
                bc = cases.get(cid)
                if bc is None:
                    continue
                plan = c.get("plan") or {}
                item = {
                    "dataset_id": (
                        f"{'valid' if c.get('overall_ok') else 'type_c'}"
                        f"::{cid}::{path.stem}"
                    ),
                    "source_kind": "historical_real",
                    "case_id_analysis_only": cid,
                    "domain_analysis_only": bc.domain,
                    "scenario_analysis_only": bc.scenario,
                    "user_prompt": bc.prompt,
                    "plan": plan,
                    "ops": list(_ops(plan)),
                    "grain": _grain(plan),
                    "source_file_analysis_only": str(path),
                }
                if c.get("overall_ok"):
                    # Diversity key: case + op family + grain (one exemplar each)
                    vkey = f"{cid}::{tuple(_ops(plan))}::{_grain(plan)}"
                    if vkey in seen_valid:
                        continue
                    seen_valid.add(vkey)
                    item["label"] = "VALID_SUCCESS"
                    valid.append(item)
                else:
                    if cid != "same_schema_union_001":
                        continue
                    # Keep multiple historical Type-C instances (stability / variance)
                    ckey = f"{cid}::{path.stem}"
                    if ckey in seen_c:
                        continue
                    seen_c.add(ckey)
                    item["label"] = "TYPE_C"
                    item["type_c_family"] = "declared_collapsed_grain_wrong_user_intent"
                    type_c.append(item)

    # synthetic_valid: deterministic fixed_plans (known-good compositions)
    for bc in cases.values():
        if not bc.fixed_plan or bc.fixed_plan.get("status") != "planned":
            continue
        if "success" not in (bc.expected.pipeline_status or ["success"]):
            continue
        plan = dict(bc.fixed_plan)
        vkey = f"fixed::{bc.id}::{tuple(_ops(plan))}::{_grain(plan)}"
        if vkey in seen_valid:
            continue
        # Skip if we already have this case+ops from live
        live_key = f"{bc.id}::{tuple(_ops(plan))}::{_grain(plan)}"
        if live_key in seen_valid:
            continue
        seen_valid.add(vkey)
        # Ensure final_output_requirements for grain diversity when missing
        if not plan.get("final_output_requirements"):
            ops = _ops(plan)
            grain = "group" if "aggregate" in ops else "detail"
            plan = {
                **plan,
                "final_output_requirements": {
                    "grain": grain,
                    "required_columns": list(
                        (bc.expected.result.required_columns or [])[:6]
                    ),
                    "one_row_represents": "benchmark fixed plan output",
                },
            }
        valid.append(
            {
                "dataset_id": f"valid_fixed::{bc.id}",
                "source_kind": "synthetic_valid",
                "case_id_analysis_only": bc.id,
                "domain_analysis_only": bc.domain,
                "scenario_analysis_only": bc.scenario,
                "user_prompt": bc.prompt,
                "plan": plan,
                "ops": list(_ops(plan)),
                "grain": _grain(plan),
                "label": "VALID_SUCCESS",
            }
        )
    return valid, type_c


def _select_diverse_valid(valid: list[dict[str, Any]], *, target: int = 60) -> list[dict[str, Any]]:
    """Prefer diversity across ops family + grain; keep legitimate aggregates."""
    if len(valid) <= target:
        return list(valid)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for v in valid:
        key = f"{tuple(v['ops'])}::{v.get('grain')}"
        buckets[key].append(v)

    selected: list[dict[str, Any]] = []
    while len(selected) < target and any(buckets.values()):
        for key in list(buckets.keys()):
            if not buckets[key]:
                continue
            selected.append(buckets[key].pop(0))
            if len(selected) >= target:
                break
        buckets = defaultdict(list, {k: v for k, v in buckets.items() if v})
    return selected


def _synthetic_type_c_from_patterns(
    valid: list[dict[str, Any]], historical_c: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Plausible planner mutations: detail/stacking request + internally consistent group+agg.

    Based on historical same_schema failure pattern (union→aggregate, grain=group).
    Marked synthetic_type_c. Uses only generic mutations — labels not fed to verifier.
    """
    out: list[dict[str, Any]] = []
    # Template wrong plan shape from a real Type-C plan
    if not historical_c:
        return out
    template = deepcopy(historical_c[0]["plan"])

    # Find valid union-only / join-only detail prompts to pair with wrong collapsed plans
    donors = [
        v
        for v in valid
        if v.get("grain") in {"detail", "entity"}
        and "aggregate" not in (v.get("ops") or [])
    ][:6]

    for i, donor in enumerate(donors):
        wrong = deepcopy(template)
        # Keep structure but ensure grain=group + aggregate present (already in template)
        req = wrong.get("final_output_requirements") or {}
        req["grain"] = "group"
        if not req.get("one_row_represents"):
            req["one_row_represents"] = "aggregated group summary"
        wrong["final_output_requirements"] = req
        out.append(
            {
                "dataset_id": f"synthetic_type_c::{donor['case_id_analysis_only']}::{i}",
                "source_kind": "synthetic_type_c",
                "case_id_analysis_only": donor["case_id_analysis_only"],
                "domain_analysis_only": donor.get("domain_analysis_only"),
                "scenario_analysis_only": donor.get("scenario_analysis_only"),
                "user_prompt": donor["user_prompt"],  # detail/stacking-style request
                "plan": wrong,  # collapsed aggregate plan
                "ops": list(_ops(wrong)),
                "grain": _grain(wrong),
                "label": "TYPE_C",
                "type_c_family": "detail_request_vs_consistent_group_aggregate",
                "mutation_note": (
                    "historical Type-C plan shape attached to a detail/non-agg "
                    "user prompt (plausible 7B failure pattern)"
                ),
            }
        )
    return out


def build_generalization_dataset() -> dict[str, Any]:
    valid_all, type_c_hist = _harvest_historical()
    valid_sel = _select_diverse_valid(valid_all, target=60)
    type_c_syn = _synthetic_type_c_from_patterns(valid_all, type_c_hist)
    type_c = type_c_hist + type_c_syn

    grains = Counter(v.get("grain") for v in valid_sel)
    ops = Counter(tuple(v.get("ops") or []) for v in valid_sel)
    domains = Counter(v.get("domain_analysis_only") for v in valid_sel)
    n_hist_valid = sum(1 for v in valid_sel if v.get("source_kind") == "historical_real")
    n_syn_valid = sum(1 for v in valid_sel if v.get("source_kind") == "synthetic_valid")

    return {
        "frozen_verifier": _prompt_fingerprint(),
        "counts": {
            "valid_count": len(valid_sel),
            "type_c_count": len(type_c),
            "historical_valid": n_hist_valid,
            "synthetic_valid": n_syn_valid,
            "historical_type_c": len(type_c_hist),
            "synthetic_type_c": len(type_c_syn),
            "historical_count": n_hist_valid + len(type_c_hist),
            "synthetic_count": n_syn_valid + len(type_c_syn),
            "valid_pool_before_select": len(valid_all),
        },
        "diversity": {
            "valid_grains": dict(grains),
            "valid_op_families": {str(k): v for k, v in ops.most_common()},
            "valid_domains": dict(domains),
            "legitimate_group_or_summary_aggregate": sum(
                1
                for v in valid_sel
                if v.get("grain") in {"group", "summary"}
                and "aggregate" in (v.get("ops") or [])
            ),
        },
        "items": valid_sel + type_c,
        "note": (
            "Analysis-only fields (*_analysis_only, label, source_kind) are never "
            "passed to the verifier. Verifier sees user_prompt + plan only."
        ),
    }


def _score_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tp = fn = tn = fp = 0
    unc_c = unc_v = parse_fail = 0
    lats: list[float] = []
    by_source: dict[str, Counter[str]] = defaultdict(Counter)

    for r in rows:
        if r.get("elapsed_s") is not None:
            lats.append(float(r["elapsed_s"]))
        v = r.get("verdict")
        label = r.get("label")
        sk = r.get("source_kind") or "unknown"
        if v == "parse_failed":
            parse_fail += 1
            by_source[sk]["parse_fail"] += 1
            continue
        if v == "uncertain":
            if label == "TYPE_C":
                unc_c += 1
                by_source[sk]["uncertain"] += 1
            else:
                unc_v += 1
            continue
        if label == "TYPE_C":
            if v == "fail":
                tp += 1
                by_source[sk]["tp"] += 1
            elif v == "pass":
                fn += 1
                by_source[sk]["fn"] += 1
        else:
            if v == "pass":
                tn += 1
                by_source[sk]["tn"] += 1
            elif v == "fail":
                fp += 1
                by_source[sk]["fp"] += 1

    def pct(num: int, den: int) -> float | None:
        return None if den == 0 else round(100.0 * num / den, 2)

    lat = {}
    if lats:
        lat = {
            "mean": round(statistics.mean(lats), 2),
            "p50": round(statistics.median(lats), 2),
            "p95": round(sorted(lats)[max(0, int(round(0.95 * (len(lats) - 1))))], 2),
            "n": len(lats),
        }

    return {
        "type_c_tp": tp,
        "type_c_fn": fn,
        "valid_tn": tn,
        "valid_fp": fp,
        "uncertain_type_c": unc_c,
        "uncertain_valid": unc_v,
        "parse_fail": parse_fail,
        "type_c_recall": pct(tp, tp + fn),
        "type_c_precision": pct(tp, tp + fp),
        "valid_accept_rate": pct(tn, tn + fp),
        "valid_fp_rate": pct(fp, tn + fp),
        "uncertain_rate": pct(unc_c + unc_v, len(rows)),
        "parse_failure_rate": pct(parse_fail, len(rows)),
        "latency": lat,
        "by_source_kind": {k: dict(v) for k, v in by_source.items()},
        "n": len(rows),
    }


def run_verification(
    items: list[dict[str, Any]],
    *,
    runs: int = 1,
    model: str = FROZEN_MODEL,
    progress_name: str = "progress.jsonl",
) -> list[dict[str, Any]]:
    OUT.mkdir(parents=True, exist_ok=True)
    progress = OUT / progress_name
    progress.write_text("", encoding="utf-8")
    rows: list[dict[str, Any]] = []
    for run in range(1, runs + 1):
        for item in items:
            print(
                f"[p34] run{run} {item['label']} {item.get('source_kind')} "
                f"{item['case_id_analysis_only']}",
                flush=True,
            )
            # Strict V1: prompt + plan only
            res = run_semantic_verification(
                user_prompt=item["user_prompt"],
                plan=item["plan"],
                result=None,
                understanding=None,
                variant=FROZEN_VARIANT,
                model=model,
            )
            row = {
                "run": run,
                "label": item["label"],
                "source_kind": item.get("source_kind"),
                "case_id_analysis_only": item["case_id_analysis_only"],
                "dataset_id": item["dataset_id"],
                "ops": item.get("ops"),
                "grain": item.get("grain"),
                "type_c_family": item.get("type_c_family"),
                **res.to_dict(),
            }
            rows.append(row)
            with progress.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"  -> {res.verdict} {res.reason_code} {res.elapsed_s}s", flush=True)
    return rows


def stability_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_id[r["dataset_id"]].append(r)
    stable = 0
    unstable = {}
    reason_stable = 0
    for did, rs in by_id.items():
        vs = [r["verdict"] for r in rs]
        if len(set(vs)) == 1:
            stable += 1
        else:
            unstable[did] = vs
        reasons = [r.get("reason_code") for r in rs]
        if len(set(reasons)) == 1:
            reason_stable += 1
    n = len(by_id)
    return {
        "n_items": n,
        "runs_per_item": max((len(v) for v in by_id.values()), default=0),
        "verdict_stable_items": stable,
        "verdict_stability_rate": round(100.0 * stable / max(n, 1), 2),
        "reason_stable_items": reason_stable,
        "reason_stability_rate": round(100.0 * reason_stable / max(n, 1), 2),
        "unstable": unstable,
    }


def analyze_fp_fn(rows: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    # Use run==1 or all unique dataset latest
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r["dataset_id"]] = r
    fps = [
        {
            "dataset_id": r["dataset_id"],
            "case_id_analysis_only": r["case_id_analysis_only"],
            "ops": r.get("ops"),
            "grain": r.get("grain"),
            "source_kind": r.get("source_kind"),
            "verdict": r["verdict"],
            "reason_code": r.get("reason_code"),
            "evidence": r.get("evidence"),
        }
        for r in latest.values()
        if r["label"] == "VALID_SUCCESS" and r["verdict"] == "fail"
    ]
    fns = [
        {
            "dataset_id": r["dataset_id"],
            "case_id_analysis_only": r["case_id_analysis_only"],
            "ops": r.get("ops"),
            "grain": r.get("grain"),
            "source_kind": r.get("source_kind"),
            "type_c_family": r.get("type_c_family"),
            "verdict": r["verdict"],
            "reason_code": r.get("reason_code"),
            "evidence": r.get("evidence"),
        }
        for r in latest.values()
        if r["label"] == "TYPE_C" and r["verdict"] == "pass"
    ]
    return fps, fns


def blanket_simulation(score: dict[str, Any], *, pipeline_s: float = BASELINE_PIPELINE_S) -> dict[str, Any]:
    lat = (score.get("latency") or {}).get("mean") or 3.6
    return {
        "policy": "every_final_candidate_success_after_deterministic_validation",
        "verifier_invocation_rate": 100.0,
        "verifier_latency_mean_s": lat,
        "estimated_total_latency_s": round(pipeline_s + lat, 2),
        "baseline_pipeline_s": pipeline_s,
        "semantic_failures_detected": score.get("type_c_tp"),
        "false_semantic_failures": score.get("valid_fp"),
        "type_c_missed": score.get("type_c_fn"),
        "architecture_note": (
            "No selective router. One verifier call per final success candidate."
        ),
    }


def selective_evidence_analysis(rows: list[dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, Any]:
    """Correlation-only: would generic plan features cover Type C without scenario routing?"""
    item_by_id = {i["dataset_id"]: i for i in items}
    # Use first run
    r1 = [r for r in rows if r.get("run", 1) == 1]
    type_c = [r for r in r1 if r["label"] == "TYPE_C"]
    valid = [r for r in r1 if r["label"] == "VALID_SUCCESS"]

    def has_agg(r: dict) -> bool:
        return "aggregate" in (r.get("ops") or [])

    def collapsed_grain(r: dict) -> bool:
        return r.get("grain") in {"group", "summary"}

    # Heuristic coverage IF we selected on aggregate+group (NOT implementing — analysis only)
    def heuristic(r: dict) -> bool:
        return has_agg(r) and collapsed_grain(r)

    c_cov = sum(1 for r in type_c if heuristic(r))
    v_hit = sum(1 for r in valid if heuristic(r))
    return {
        "question": "Does a generic aggregate+collapsed-grain filter reduce invocations?",
        "type_c_covered_by_heuristic": c_cov,
        "type_c_total": len(type_c),
        "type_c_coverage_pct": round(100.0 * c_cov / max(len(type_c), 1), 2),
        "valid_also_matching_heuristic": v_hit,
        "valid_total": len(valid),
        "valid_invocation_if_heuristic_pct": round(100.0 * v_hit / max(len(valid), 1), 2),
        "assessment": (
            "Heuristic covers most Type C but also hits many legitimate "
            "group/summary aggregates — selective gate would still invoke often "
            "OR require semantic routing. Blanket cheap verify (~+3–5s) is simpler."
        ),
        "no_router_implemented": True,
    }


def layered_coverage() -> dict[str, Any]:
    return {
        "layers": [
            {
                "failure_family": "invalid key/schema/cardinality/unsafe join",
                "primary_detector": "Plan Validator",
            },
            {
                "failure_family": "execution contract / materialization failure",
                "primary_detector": "Executor / Result Validator",
            },
            {
                "failure_family": "declared grain contradiction (Type D)",
                "primary_detector": "Plan Validator (Phase 30 final_grain_contradiction)",
            },
            {
                "failure_family": "plan-vs-user semantic mismatch (Type C)",
                "primary_detector": "Semantic Verifier candidate (7B V1)",
            },
            {
                "failure_family": "undeclared output requirement (Type B)",
                "primary_detector": "unresolved (not Phase 34 target)",
            },
        ],
        "overlap_note": (
            "Semantic verifier must not duplicate Type D deterministic checks; "
            "it targets internally consistent but wrong intent."
        ),
    }


def write_static(dataset: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "phase33_verifier_freeze.json").write_text(
        json.dumps(dataset["frozen_verifier"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    slim_items = []
    for it in dataset["items"]:
        slim_items.append(
            {
                k: v
                for k, v in it.items()
                if k != "plan"
            }
            | {"plan_ops": it.get("ops"), "plan_grain": it.get("grain")}
        )
    slim = {**dataset, "items": slim_items}
    (OUT / "generalization_dataset.json").write_text(
        json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "generalization_dataset_full.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "layered_coverage.json").write_text(
        json.dumps(layered_coverage(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--stability-runs", type=int, default=0)
    ap.add_argument("--stability-size", type=int, default=24)
    args = ap.parse_args()

    ds = build_generalization_dataset()
    write_static(ds)
    print(json.dumps(ds["counts"], indent=2))
    print(json.dumps(ds["diversity"], indent=2))
    if args.build_only:
        raise SystemExit(0)

    rows = run_verification(ds["items"], runs=args.runs, progress_name="main_progress.jsonl")
    score = _score_rows([r for r in rows if r.get("run") == 1] if args.runs > 1 else rows)
    # If multi-run main, score on run1 only for primary metrics
    if args.runs == 1:
        score = _score_rows(rows)
    else:
        score = _score_rows([r for r in rows if r["run"] == 1])

    fps, fns = analyze_fp_fn(rows)
    (OUT / "valid_stress_results.json").write_text(
        json.dumps(
            {
                "score_valid_slice": _score_rows(
                    [r for r in rows if r["label"] == "VALID_SUCCESS" and r.get("run", 1) == 1]
                ),
                "rows": [r for r in rows if r["label"] == "VALID_SUCCESS"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (OUT / "type_c_stress_results.json").write_text(
        json.dumps(
            {
                "score_type_c_slice": _score_rows(
                    [r for r in rows if r["label"] == "TYPE_C" and r.get("run", 1) == 1]
                ),
                "historical": _score_rows(
                    [
                        r
                        for r in rows
                        if r["label"] == "TYPE_C"
                        and r.get("source_kind") == "historical_real"
                        and r.get("run", 1) == 1
                    ]
                ),
                "synthetic": _score_rows(
                    [
                        r
                        for r in rows
                        if r["label"] == "TYPE_C"
                        and r.get("source_kind") == "synthetic_type_c"
                        and r.get("run", 1) == 1
                    ]
                ),
                "rows": [r for r in rows if r["label"] == "TYPE_C"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (OUT / "false_positive_analysis.json").write_text(
        json.dumps({"count": len(fps), "traces": fps}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT / "false_negative_analysis.json").write_text(
        json.dumps({"count": len(fns), "traces": fns}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT / "verifier_latency.json").write_text(
        json.dumps(score.get("latency"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "main_score.json").write_text(
        json.dumps(score, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "blanket_policy_simulation.json").write_text(
        json.dumps(blanket_simulation(score), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT / "selective_evidence_analysis.json").write_text(
        json.dumps(
            selective_evidence_analysis(rows, ds["items"]), ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )

    if args.stability_runs >= 2:
        # Subset: all type_c + sample valid
        type_c_items = [i for i in ds["items"] if i["label"] == "TYPE_C"]
        valid_items = [i for i in ds["items"] if i["label"] == "VALID_SUCCESS"]
        # include legitimate aggregates in stability valid sample
        agg_valid = [v for v in valid_items if "aggregate" in (v.get("ops") or [])]
        non_agg = [v for v in valid_items if "aggregate" not in (v.get("ops") or [])]
        n_valid = max(0, args.stability_size - len(type_c_items))
        stab_items = type_c_items + (agg_valid[: n_valid // 2] + non_agg[: n_valid - n_valid // 2])
        stab_rows = run_verification(
            stab_items,
            runs=args.stability_runs,
            progress_name="stability_progress.jsonl",
        )
        stab = stability_from_rows(stab_rows)
        (OUT / "verifier_stability.json").write_text(
            json.dumps({"stability": stab, "n_items": len(stab_items)}, indent=2),
            encoding="utf-8",
        )

    print(json.dumps(score, indent=2))
