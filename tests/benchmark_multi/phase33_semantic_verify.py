"""Phase 33 — Semantic verification research harness (offline only)."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.relationship_infer import build_cross_file_understanding
from core.integrate.semantic_verifier import (
    VERIFIER_SYSTEM_PROMPT,
    run_semantic_verification,
)
from tests.benchmark_multi import DATASETS_DIR
from tests.benchmark_multi.generate_datasets import ensure_datasets
from tests.benchmark_multi.schema import load_all_cases

OUT = Path("benchmark_results/multi/phase33")

SOURCES = [
    Path("benchmark_results/multi/phase27/qwen2.5_7b/full_19"),
    Path("benchmark_results/multi/phase28/live_escalation"),
    Path("benchmark_results/multi/phase30/live_grain_hardening"),
]

# Balanced probe case ids (analysis labels only — never sent to verifier)
_SILENT_PREF = [
    "same_schema_union_001",  # Type C
    "three_file_chain_001",  # Type B
    "composite_key_join_001",  # Type D control (pre-P30 silents)
]
_VALID_PREF = [
    "compatible_schema_union_001",
    "master_detail_join_001",
    "lookup_join_001",
    "join_aggregate_001",
    "union_aggregate_001",
    "filter_union_aggregate_001",
    "dirty_multifile_001",
    "rename_join_001",
    "partial_overlap_join_001",
    "composite_key_join_001",  # post-P30 valid
]


def _type_label(case_id: str, cats: list[str], overall_ok: bool) -> str | None:
    if overall_ok:
        return None
    if case_id == "same_schema_union_001":
        return "C"
    if case_id == "three_file_chain_001":
        return "B"
    if case_id == "composite_key_join_001":
        return "D"
    if "correct_op_grain_mismatch" in cats:
        return "C"
    if "correct_op_structural_mismatch" in cats:
        return "B"
    return "unknown"


def _result_from_case(c: dict[str, Any]) -> dict[str, Any]:
    meta = c.get("metadata") or {}
    shape = meta.get("final_shape")
    columns: list[str] = []
    actual = meta.get("actual_schema_by_step") or []
    if isinstance(actual, list) and actual:
        last = actual[-1]
        if isinstance(last, dict):
            cols = last.get("columns") or last.get("column_names") or []
            if isinstance(cols, dict):
                columns = list(cols.keys())
            elif isinstance(cols, list):
                columns = [str(x) if not isinstance(x, dict) else str(x.get("name")) for x in cols]
    # Fallback from plan requirements
    if not columns:
        req = ((c.get("plan") or {}).get("final_output_requirements") or {}).get(
            "required_columns"
        ) or []
        columns = [str(x) for x in req]
    row_count = None
    if isinstance(shape, list) and len(shape) >= 1:
        row_count = shape[0]
        if len(shape) >= 2 and not columns:
            # only have count
            pass
    return {
        "columns": columns,
        "row_count": row_count,
        "sample_rows": [],  # schema+count first; samples optional later
    }


def _understanding_for_case(case_id: str) -> dict[str, Any] | None:
    ensure_datasets(DATASETS_DIR, force=False)
    case = next((c for c in load_all_cases() if c.id == case_id), None)
    if case is None:
        return None
    sources = {Path(f).stem: pd.read_excel(DATASETS_DIR / f) for f in case.files}
    und = build_cross_file_understanding(
        list(sources.items()), infer_relationships=False
    ).to_dict()
    if case.fixed_relationships:
        und["relationships"] = list(case.fixed_relationships)
    return und


def build_verification_dataset() -> dict[str, Any]:
    cases_by_id = {c.id: c for c in load_all_cases()}
    silent: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    seen_silent: set[tuple[str, str]] = set()
    seen_valid: set[tuple[str, str]] = set()

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
                ok = bool(c.get("overall_ok"))
                key = (cid, path.name)
                bc = cases_by_id.get(cid)
                if bc is None:
                    continue
                item = {
                    "dataset_id": f"{'valid' if ok else 'silent'}::{cid}::{path.stem}",
                    "label": "VALID_SUCCESS" if ok else "SILENT_WRONG_SUCCESS",
                    "phase29_type": _type_label(
                        cid, list(c.get("failure_categories") or []), ok
                    ),
                    "case_id_analysis_only": cid,
                    "source_file_analysis_only": str(path),
                    "user_prompt": bc.prompt,
                    "plan": c.get("plan"),
                    "result": _result_from_case(c),
                    "selected_operations": c.get("selected_operations"),
                }
                if ok:
                    if key in seen_valid:
                        continue
                    seen_valid.add(key)
                    valid.append(item)
                else:
                    if key in seen_silent:
                        continue
                    seen_silent.add(key)
                    silent.append(item)

    # Balanced probe selection — emphasize Type B/C residual; Type D as control
    probe_silent: list[dict[str, Any]] = []
    quotas = {
        "same_schema_union_001": 4,  # Type C
        "three_file_chain_001": 2,  # Type B
        "composite_key_join_001": 2,  # Type D control
    }
    for pref, n in quotas.items():
        got = 0
        for s in silent:
            if s["case_id_analysis_only"] == pref and s not in probe_silent:
                probe_silent.append(s)
                got += 1
                if got >= n:
                    break
    for s in silent:
        if len(probe_silent) >= 8:
            break
        if s not in probe_silent:
            probe_silent.append(s)

    probe_valid: list[dict[str, Any]] = []
    for pref in _VALID_PREF:
        for v in valid:
            if v["case_id_analysis_only"] == pref and all(
                x["case_id_analysis_only"] != pref for x in probe_valid
            ):
                # prefer phase30 composite valid
                if pref == "composite_key_join_001" and "phase30" not in v[
                    "source_file_analysis_only"
                ]:
                    continue
                probe_valid.append(v)
                break
    for v in valid:
        if len(probe_valid) >= 12:
            break
        if all(x["dataset_id"] != v["dataset_id"] for x in probe_valid):
            # diversify
            if any(
                x["case_id_analysis_only"] == v["case_id_analysis_only"]
                for x in probe_valid
            ):
                continue
            probe_valid.append(v)

    # Attach understanding for V3 (rebuild; no golden)
    und_cache: dict[str, dict[str, Any]] = {}
    for item in probe_silent + probe_valid:
        cid = item["case_id_analysis_only"]
        if cid not in und_cache:
            und_cache[cid] = _understanding_for_case(cid) or {}
        item["understanding"] = und_cache[cid]

    type_counts = Counter(s.get("phase29_type") for s in probe_silent)
    return {
        "note": (
            "Labels (VALID/SILENT, case_id, phase29_type, source_file) are for "
            "offline evaluation only and must never be passed to the verifier."
        ),
        "corpus_totals": {
            "valid_success": len(valid),
            "silent_wrong_success": len(silent),
        },
        "probe": {
            "valid_success_count": len(probe_valid),
            "silent_wrong_count": len(probe_silent),
            "type_b_count": type_counts.get("B", 0),
            "type_c_count": type_counts.get("C", 0),
            "type_d_count": type_counts.get("D", 0),
        },
        "items": probe_silent + probe_valid,
        "verifier_system_prompt": VERIFIER_SYSTEM_PROMPT,
    }


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Positive class = silent wrong. FAIL on silent = TP; FAIL on valid = FP."""
    tp = fp = tn = fn = 0
    uncertain_s = uncertain_v = 0
    parse_fail = 0
    latencies: list[float] = []
    by_type: dict[str, Counter[str]] = defaultdict(Counter)

    for r in rows:
        v = r.get("verdict")
        label = r.get("label")
        t = r.get("phase29_type")
        if r.get("elapsed_s") is not None:
            latencies.append(float(r["elapsed_s"]))
        if v == "parse_failed":
            parse_fail += 1
            continue
        if v == "uncertain":
            if label == "SILENT_WRONG_SUCCESS":
                uncertain_s += 1
                if t:
                    by_type[t]["uncertain"] += 1
            else:
                uncertain_v += 1
            continue
        if label == "SILENT_WRONG_SUCCESS":
            if v == "fail":
                tp += 1
                if t:
                    by_type[t]["tp"] += 1
            elif v == "pass":
                fn += 1
                if t:
                    by_type[t]["fn"] += 1
        else:
            if v == "pass":
                tn += 1
            elif v == "fail":
                fp += 1

    silent_n = tp + fn + uncertain_s
    valid_n = tn + fp + uncertain_v
    wrong_recall = tp / (tp + fn) if (tp + fn) else None
    # precision among decided fails
    wrong_precision = tp / (tp + fp) if (tp + fp) else None
    valid_fp_rate = fp / (tn + fp) if (tn + fp) else None
    valid_accept = tn / (tn + fp) if (tn + fp) else None
    decided = tp + fp + tn + fn
    accuracy = (tp + tn) / decided if decided else None

    def _pct(x: float | None) -> float | None:
        return None if x is None else round(100.0 * x, 2)

    lat = {}
    if latencies:
        lat = {
            "mean": round(statistics.mean(latencies), 2),
            "p50": round(statistics.median(latencies), 2),
            "p95": round(
                sorted(latencies)[max(0, int(round(0.95 * (len(latencies) - 1))))], 2
            ),
            "n": len(latencies),
        }

    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "uncertain_silent": uncertain_s,
        "uncertain_valid": uncertain_v,
        "uncertain_rate": round(
            100.0 * (uncertain_s + uncertain_v) / max(len(rows), 1), 2
        ),
        "parse_fail": parse_fail,
        "parse_fail_rate": round(100.0 * parse_fail / max(len(rows), 1), 2),
        "silent_wrong_recall": _pct(wrong_recall),
        "silent_wrong_precision": _pct(wrong_precision),
        "valid_success_accept_rate": _pct(valid_accept),
        "valid_success_false_positive_rate": _pct(valid_fp_rate),
        "accuracy_excluding_uncertain": _pct(accuracy),
        "latency": lat,
        "by_type": {k: dict(v) for k, v in by_type.items()},
        "n_rows": len(rows),
        "silent_n": silent_n,
        "valid_n": valid_n,
    }


def run_matrix(
    dataset: dict[str, Any],
    *,
    models: list[str],
    variants: list[str],
    runs: int = 1,
) -> dict[str, Any]:
    items = dataset["items"]
    all_rows: list[dict[str, Any]] = []
    progress = OUT / "verifier_progress.jsonl"
    OUT.mkdir(parents=True, exist_ok=True)
    progress.write_text("", encoding="utf-8")

    for model in models:
        for variant in variants:
            for run in range(1, runs + 1):
                for item in items:
                    # Strip analysis-only fields from what we pass
                    print(
                        f"[verify] {model} {variant} run{run} "
                        f"{item['label']} {item['case_id_analysis_only']}",
                        flush=True,
                    )
                    res = run_semantic_verification(
                        user_prompt=item["user_prompt"],
                        plan=item["plan"],
                        result=item.get("result"),
                        understanding=item.get("understanding"),
                        variant=variant,
                        model=model,
                    )
                    row = {
                        "model": model,
                        "variant": variant,
                        "run": run,
                        "label": item["label"],
                        "phase29_type": item.get("phase29_type"),
                        "case_id_analysis_only": item["case_id_analysis_only"],
                        "dataset_id": item["dataset_id"],
                        **res.to_dict(),
                    }
                    all_rows.append(row)
                    with progress.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    print(
                        f"  -> {res.verdict} {res.reason_code} {res.elapsed_s}s",
                        flush=True,
                    )

    comparison: dict[str, Any] = {}
    for model in models:
        for variant in variants:
            subset = [
                r
                for r in all_rows
                if r["model"] == model and r["variant"] == variant
            ]
            comparison[f"{model}::{variant}"] = _metrics(subset)

    return {"rows": all_rows, "comparison": comparison}


def stability_report(rows: list[dict[str, Any]], *, model: str, variant: str) -> dict[str, Any]:
    subset = [r for r in rows if r["model"] == model and r["variant"] == variant]
    by_id: dict[str, list[str]] = defaultdict(list)
    for r in subset:
        by_id[r["dataset_id"]].append(r["verdict"])
    stable = sum(1 for vs in by_id.values() if len(set(vs)) == 1)
    return {
        "model": model,
        "variant": variant,
        "n_items": len(by_id),
        "stable_items": stable,
        "stability_rate": round(100.0 * stable / max(len(by_id), 1), 2),
        "unstable": {
            k: v for k, v in by_id.items() if len(set(v)) > 1
        },
    }


def cost_simulation(comparison: dict[str, Any]) -> dict[str, Any]:
    baseline = 34.14
    out = {"baseline_pipeline_s": baseline, "configs": {}}
    for key, m in comparison.items():
        lat = (m.get("latency") or {}).get("mean")
        if lat is None:
            continue
        out["configs"][key] = {
            "verifier_mean_s": lat,
            "every_success_hypothetical_total_s": round(baseline + lat, 2),
            "note": "Assumes verifier on every pipeline success; not production policy",
        }
    return out


def write_artifacts(dataset: dict[str, Any], matrix: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # Strip bulky understanding from saved dataset display? keep for reproducibility
    slim_items = []
    for it in dataset["items"]:
        slim = {
            k: v
            for k, v in it.items()
            if k not in {"understanding"}
        }
        slim["has_understanding"] = bool(it.get("understanding"))
        slim_items.append(slim)
    ds_out = {**dataset, "items": slim_items}
    (OUT / "verification_dataset.json").write_text(
        json.dumps(ds_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "verifier_model_comparison.json").write_text(
        json.dumps(matrix["comparison"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "verifier_confusion_matrix.json").write_text(
        json.dumps(
            {
                k: {
                    "tp": v["tp"],
                    "fp": v["fp"],
                    "tn": v["tn"],
                    "fn": v["fn"],
                    "uncertain_silent": v["uncertain_silent"],
                    "uncertain_valid": v["uncertain_valid"],
                    "parse_fail": v["parse_fail"],
                    "by_type": v["by_type"],
                }
                for k, v in matrix["comparison"].items()
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    lat = {
        k: v.get("latency") for k, v in matrix["comparison"].items()
    }
    (OUT / "verifier_latency.json").write_text(
        json.dumps(lat, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "hypothetical_cost_simulation.json").write_text(
        json.dumps(cost_simulation(matrix["comparison"]), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # context probe = comparison keyed by variant
    ctx = defaultdict(dict)
    for k, v in matrix["comparison"].items():
        model, variant = k.split("::", 1)
        ctx[variant][model] = {
            "silent_wrong_recall": v["silent_wrong_recall"],
            "silent_wrong_precision": v["silent_wrong_precision"],
            "valid_fp": v["valid_success_false_positive_rate"],
            "uncertain_rate": v["uncertain_rate"],
            "latency_mean": (v.get("latency") or {}).get("mean"),
        }
    (OUT / "verifier_context_probe.json").write_text(
        json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "verifier_raw_rows.json").write_text(
        json.dumps(matrix["rows"], ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--models", default="qwen2.5:7b,qwen3:32b")
    ap.add_argument("--variants", default="V1,V2,V3")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--stability-runs", type=int, default=0)
    args = ap.parse_args()

    ds = build_verification_dataset()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.build_only:
        write_artifacts(ds, {"rows": [], "comparison": {}})
        print(json.dumps(ds["probe"], indent=2))
        raise SystemExit(0)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    matrix = run_matrix(ds, models=models, variants=variants, runs=args.runs)
    write_artifacts(ds, matrix)

    if args.stability_runs > 1:
        # re-run promising configs only — caller passes --stability-runs with filtered models
        stab_matrix = run_matrix(
            ds, models=models, variants=variants, runs=args.stability_runs
        )
        stabs = []
        for model in models:
            for variant in variants:
                stabs.append(
                    stability_report(stab_matrix["rows"], model=model, variant=variant)
                )
        (OUT / "verifier_stability.json").write_text(
            json.dumps(stabs, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(json.dumps(matrix["comparison"], indent=2))
