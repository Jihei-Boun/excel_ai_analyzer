"""Phase 32 — Planner output-contract declaration improvement (prompt A/B).

Does not change DSL / validators / escalation / evaluator / route_multi.
Uses diagnostic structural labels only for offline scoring.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.integration_planner import (
    _PLANNER_SYSTEM_BASELINE,
    _PLANNER_SYSTEM_CANDIDATE_A,
    build_integration_plan,
    get_planner_system_prompt,
    planner_prompt_token_estimate,
)
from core.integrate.relationship_infer import build_cross_file_understanding
from tests.benchmark_multi import DATASETS_DIR
from tests.benchmark_multi.generate_datasets import ensure_datasets
from tests.benchmark_multi.schema import load_all_cases

OUT = Path("benchmark_results/multi/phase32")

# Cases with structural required_columns for declaration scoring
_LABEL_CASES = [
    "same_schema_union_001",
    "compatible_schema_union_001",
    "master_detail_join_001",
    "lookup_join_001",
    "join_aggregate_001",
    "union_aggregate_001",
    "filter_union_aggregate_001",
    "composite_key_join_001",
    "dirty_multifile_001",
    "three_file_chain_001",
    "rename_join_001",
    "partial_overlap_join_001",
]

_SAFETY_CASES = [
    "ambiguous_keys_001",
    "many_to_many_001",
    "incompatible_union_001",
    "unrelated_files_001",
    "impossible_aggregate_001",
]

_TYPE_B = "three_file_chain_001"


def _structural_expected(case: Any) -> set[str]:
    req = list(case.expected.result.required_columns or [])
    metric_aliases: set[str] = set()
    for m in case.expected.result.expected_metrics or []:
        if m.get("alias"):
            metric_aliases.add(str(m["alias"]))
    for step in (case.fixed_plan or {}).get("steps") or []:
        if step.get("op") != "aggregate":
            continue
        for m in (step.get("params") or {}).get("metrics") or []:
            if isinstance(m, dict) and m.get("alias"):
                metric_aliases.add(str(m["alias"]))
    return {c for c in req if c not in metric_aliases}


def _understanding(case: Any) -> dict[str, Any]:
    ensure_datasets(DATASETS_DIR, force=False)
    sources = {Path(f).stem: pd.read_excel(DATASETS_DIR / f) for f in case.files}
    und = build_cross_file_understanding(
        list(sources.items()), infer_relationships=False
    ).to_dict()
    if case.fixed_relationships:
        und["relationships"] = list(case.fixed_relationships)
    return und


def baseline_freeze() -> dict[str, Any]:
    return {
        "phase": 32,
        "from": "phase30_live_grain_hardening",
        "overall_ok": 89.47,
        "safe_outcome": 96.49,
        "unsafe_execution": 0.0,
        "escalation_rate": 17.54,
        "strong_planner_invocation_rate": 17.54,
        "escalation_success_rate": 15.79,
        "first_plan_success_rate": 59.65,
        "retry_success_rate": 31.58,
        "retry_exhausted_rate": 10.53,
        "composite_final": 100.0,
        "three_file_final": 0.0,
        "latency_est_s": 34.14,
        "note": "Phase 31 diagnostic-only; identical to Phase 30 live baseline",
    }


def prompt_candidates_meta() -> dict[str, Any]:
    b = planner_prompt_token_estimate(_PLANNER_SYSTEM_BASELINE)
    c = planner_prompt_token_estimate(_PLANNER_SYSTEM_CANDIDATE_A)
    return {
        "baseline": {
            "id": "baseline_phase30_31",
            "description": "Existing Final-output-aware planning prompt",
            "length": b,
        },
        "candidate_a": {
            "id": "candidate_a_answer_fields_vs_mechanics",
            "description": (
                "Compact addition: required_columns = reader-facing answer fields; "
                "join keys ≠ automatic required; match what user asked to see; "
                "complete-but-minimal; answer-completeness self-check"
            ),
            "length": c,
            "delta_chars": c["chars"] - b["chars"],
            "delta_approx_tokens": c["approx_tokens"] - b["approx_tokens"],
            "no_scenario_domain_column_hardcoding": True,
        },
        "hypothesis": (
            "Sharpening answer-field vs mechanics + completeness self-check "
            "reduces Type-B under-declaration on 7B without large over-declaration"
        ),
    }


def _score_declaration(declared: set[str], expected: set[str]) -> dict[str, Any]:
    if not expected and not declared:
        return {
            "tp": 0,
            "fp": 0,
            "fn": 0,
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "under": [],
            "over": [],
        }
    tp = len(declared & expected)
    fp = len(declared - expected)
    fn = len(expected - declared)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (
        2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    )
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "under": sorted(expected - declared),
        "over": sorted(declared - expected),
    }


def run_declaration_probe(
    *,
    model: str = "qwen2.5:7b",
    variants: list[str] | None = None,
    case_ids: list[str] | None = None,
) -> dict[str, Any]:
    variants = variants or ["baseline", "candidate_a"]
    case_ids = case_ids or (_LABEL_CASES + _SAFETY_CASES)
    cases = {c.id: c for c in load_all_cases()}
    rows: list[dict[str, Any]] = []
    progress = OUT / "declaration_probe_progress.jsonl"
    OUT.mkdir(parents=True, exist_ok=True)
    progress.write_text("", encoding="utf-8")

    for variant in variants:
        system = get_planner_system_prompt(variant=variant)
        for cid in case_ids:
            case = cases.get(cid)
            if case is None:
                continue
            expected = _structural_expected(case)
            und = _understanding(case)
            print(f"[probe] {variant} {cid}", flush=True)
            t0 = time.time()
            plan = build_integration_plan(
                case.prompt, und, model=model, system_prompt=system
            )
            elapsed = round(time.time() - t0, 2)
            req = (
                plan.final_output_requirements.to_dict()
                if plan.final_output_requirements
                and not plan.final_output_requirements.is_empty
                else None
            )
            declared = set((req or {}).get("required_columns") or [])
            score = _score_declaration(declared, expected) if expected else None
            row = {
                "variant": variant,
                "case_id": cid,
                "elapsed_s": elapsed,
                "status": plan.status,
                "ops": [s.op for s in plan.steps],
                "declared": req,
                "structural_expected": sorted(expected),
                "score": score,
                "is_type_b": cid == _TYPE_B,
                "is_safety": cid in _SAFETY_CASES,
            }
            rows.append(row)
            with progress.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            under = (score or {}).get("under")
            print(
                f"  status={plan.status} recall={(score or {}).get('recall')} "
                f"under={under} elapsed={elapsed}s",
                flush=True,
            )

    summary: dict[str, Any] = {}
    for variant in variants:
        labeled = [
            r
            for r in rows
            if r["variant"] == variant
            and r["score"] is not None
            and r["status"] == "planned"
        ]
        if not labeled:
            summary[variant] = {"n_planned_labeled": 0}
            continue
        n = len(labeled)
        avg_p = sum(r["score"]["precision"] for r in labeled) / n
        avg_r = sum(r["score"]["recall"] for r in labeled) / n
        avg_f = sum(r["score"]["f1"] for r in labeled) / n
        under_rate = sum(1 for r in labeled if r["score"]["under"]) / n
        over_rate = sum(1 for r in labeled if r["score"]["over"]) / n
        type_b = next(
            (r for r in rows if r["variant"] == variant and r["case_id"] == _TYPE_B),
            None,
        )
        cannot = sum(
            1 for r in rows if r["variant"] == variant and r["status"] != "planned"
        )
        summary[variant] = {
            "n_planned_labeled": n,
            "required_field_precision": round(avg_p, 4),
            "required_field_recall": round(avg_r, 4),
            "required_field_f1": round(avg_f, 4),
            "under_declaration_rate": round(100.0 * under_rate, 2),
            "over_declaration_rate": round(100.0 * over_rate, 2),
            "cannot_plan_or_failed_count": cannot,
            "type_b": {
                "status": type_b["status"] if type_b else None,
                "declared": type_b["declared"] if type_b else None,
                "score": type_b["score"] if type_b else None,
                "ops": type_b["ops"] if type_b else None,
            },
        }

    diffs: list[dict[str, Any]] = []
    by_key = {(r["variant"], r["case_id"]): r for r in rows}
    for cid in case_ids:
        b = by_key.get(("baseline", cid))
        c = by_key.get(("candidate_a", cid))
        if not b or not c:
            continue
        bcols = set(((b.get("declared") or {}) or {}).get("required_columns") or [])
        ccols = set(((c.get("declared") or {}) or {}).get("required_columns") or [])
        expected = set(b.get("structural_expected") or [])
        added = sorted(ccols - bcols)
        removed = sorted(bcols - ccols)
        diffs.append(
            {
                "case_id": cid,
                "baseline_required_columns": sorted(bcols),
                "candidate_required_columns": sorted(ccols),
                "added": added,
                "removed": removed,
                "diagnostic_required": sorted(expected),
                "correct_additions": sorted(set(added) & expected),
                "incorrect_additions": sorted(set(added) - expected),
                "incorrect_removals": sorted(set(removed) & expected),
                "baseline_status": b["status"],
                "candidate_status": c["status"],
                "baseline_recall": (b.get("score") or {}).get("recall"),
                "candidate_recall": (c.get("score") or {}).get("recall"),
            }
        )

    return {
        "model": model,
        "prompt_meta": prompt_candidates_meta(),
        "summary": summary,
        "rows": rows,
        "diffs": diffs,
    }


def write_static_artifacts() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "baseline_freeze.json").write_text(
        json.dumps(baseline_freeze(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "prompt_candidates.json").write_text(
        json.dumps(prompt_candidates_meta(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT / "baseline_prompt.txt").write_text(_PLANNER_SYSTEM_BASELINE, encoding="utf-8")
    (OUT / "candidate_a_prompt.txt").write_text(
        _PLANNER_SYSTEM_CANDIDATE_A, encoding="utf-8"
    )


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--model", default="qwen2.5:7b")
    args = ap.parse_args()
    write_static_artifacts()
    if args.probe:
        result = run_declaration_probe(model=args.model)
        (OUT / "declaration_probe.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (OUT / "declaration_diff.json").write_text(
            json.dumps(result["diffs"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
