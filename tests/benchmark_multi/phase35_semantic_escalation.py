"""Phase 35 — Semantic verification-triggered escalation experiment harness."""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.relationship_infer import build_cross_file_understanding
from core.integrate.semantic_escalation import (
    SemanticEscalationConfig,
    build_semantic_replan_feedback,
    run_integration_pipeline_semantic_experimental,
)
from core.integrate.semantic_verifier import run_semantic_verification
from core.integrate.integration_pipeline import _run_integration_attempt_loop
from core.integrate.integration_plan_types import integration_plan_from_dict
from tests.benchmark_multi import DATASETS_DIR
from tests.benchmark_multi.evaluate import evaluate_case
from tests.benchmark_multi.generate_datasets import ensure_datasets
from tests.benchmark_multi.schema import load_all_cases
from tests.benchmark_multi.runner import run_suite
from core.integrate.planner_model_strategy import PlannerModelStrategy

OUT = Path("benchmark_results/multi/phase35")
P34_FULL = Path("benchmark_results/multi/phase34/generalization_dataset_full.json")


def baseline_freeze() -> dict[str, Any]:
    return {
        "phase": 35,
        "from": "phase30_live_grain_hardening",
        "overall_ok": 89.47,
        "safe_outcome": 96.49,
        "unsafe_execution": 0.0,
        "failure_32b_invocation": 17.54,
        "semantic_32b_invocation": 0.0,
        "total_32b_invocation": 17.54,
        "verifier_invocation": 0.0,
        "latency_est_s": 34.14,
    }


def _load_p34_items() -> list[dict[str, Any]]:
    if not P34_FULL.is_file():
        return []
    return list(json.loads(P34_FULL.read_text(encoding="utf-8")).get("items") or [])


def _sources_for(case_id: str) -> tuple[Any, dict[str, pd.DataFrame], dict[str, Any]]:
    ensure_datasets(DATASETS_DIR, force=False)
    case = next(c for c in load_all_cases() if c.id == case_id)
    sources = {Path(f).stem: pd.read_excel(DATASETS_DIR / f) for f in case.files}
    und = build_cross_file_understanding(
        list(sources.items()), infer_relationships=False
    ).to_dict()
    if case.fixed_relationships:
        und["relationships"] = list(case.fixed_relationships)
    return case, sources, und


def offline_replan_from_item(item: dict[str, Any]) -> dict[str, Any]:
    """Verifier on frozen plan → one 32B replan → evaluate (targeted recovery)."""
    case, sources, und = _sources_for(item["case_id_analysis_only"])
    t0 = time.time()
    ver = run_semantic_verification(
        user_prompt=item["user_prompt"],
        plan=item["plan"],
        variant="V1",
        model="qwen2.5:7b",
    )
    t_ver = time.time() - t0
    row: dict[str, Any] = {
        "dataset_id": item["dataset_id"],
        "label": item["label"],
        "source_kind": item.get("source_kind"),
        "case_id": item["case_id_analysis_only"],
        "verifier": ver.to_dict(),
        "verifier_elapsed_s": round(t_ver, 2),
        "semantic_escalated": False,
        "category": None,
    }
    escalate = ver.verdict == "fail" or (
        ver.verdict == "uncertain"
    )  # FAIL-or-UNCERTAIN policy for targeted
    if not escalate:
        row["category"] = (
            "accept_pass"
            if item["label"] == "VALID_SUCCESS"
            else "missed_type_c_no_escalate"
        )
        # Evaluate frozen original plan via deterministic fixed chat? skip — use label
        row["original_overall_ok"] = item["label"] == "VALID_SUCCESS"
        row["final_overall_ok"] = row["original_overall_ok"]
        return row

    row["semantic_escalated"] = True
    feedback = build_semantic_replan_feedback(
        previous_plan=item["plan"], verification=ver
    )
    t1 = time.time()
    strong = _run_integration_attempt_loop(
        item["user_prompt"],
        sources,
        und,
        max_retries=2,
        base_url="http://localhost:11434",
        model="qwen3:32b",
        chat_json_fn=None,
        build_plan_fn=None,
        initial_feedback=feedback,
        path_label="semantic_strong",
    )
    t_strong = time.time() - t1
    row["strong_elapsed_s"] = round(t_strong, 2)
    row["strong_status"] = strong.status
    row["strong_plan"] = strong.plan.to_dict() if strong.plan else None
    row["strong_ops"] = [
        s.op for s in (strong.plan.steps if strong.plan else [])
    ]

    # Evaluate strong result with benchmark evaluator
    ev = evaluate_case(case, pipeline=strong, understanding=und)
    row["final_overall_ok"] = bool(ev.get("overall_ok"))
    row["final_safe"] = bool(ev.get("safe_outcome"))
    row["final_unsafe"] = bool(ev.get("unsafe_execution"))
    row["final_status"] = ev.get("status")
    row["failure_categories"] = ev.get("failure_categories")

    was_wrong = item["label"] == "TYPE_C"
    was_valid = item["label"] == "VALID_SUCCESS"
    if was_wrong and ver.verdict == "fail":
        row["true_semantic_escalation"] = True
        if row["final_overall_ok"]:
            row["category"] = "successful_semantic_recovery"
        else:
            row["category"] = "failed_semantic_recovery"
    elif was_valid and escalate:
        row["false_semantic_escalation"] = True
        if row["final_overall_ok"]:
            row["category"] = "false_escalation_harmless"
        else:
            row["category"] = "harmful_false_escalation"
    else:
        row["category"] = f"other_{ver.verdict}_{item['label']}"
    return row


def run_targeted_recovery(*, limit: int | None = None) -> dict[str, Any]:
    items = _load_p34_items()
    # Prefer historical TYPE_C + historical VALID + some synthetic
    hist_c = [i for i in items if i["label"] == "TYPE_C" and i.get("source_kind") == "historical_real"]
    hist_v = [i for i in items if i["label"] == "VALID_SUCCESS" and i.get("source_kind") == "historical_real"]
    syn_c = [i for i in items if i["label"] == "TYPE_C" and i.get("source_kind") == "synthetic_type_c"]
    syn_v = [i for i in items if i["label"] == "VALID_SUCCESS" and i.get("source_kind") == "synthetic_valid"]
    # Sample valids for FP stress (all hist + up to 8 synthetic)
    selected = hist_c + hist_v[:21] + syn_c + syn_v[:8]
    if limit:
        selected = selected[:limit]

    rows = []
    OUT.mkdir(parents=True, exist_ok=True)
    progress = OUT / "targeted_progress.jsonl"
    progress.write_text("", encoding="utf-8")
    for it in selected:
        print(
            f"[p35 targeted] {it['label']} {it.get('source_kind')} {it['case_id_analysis_only']}",
            flush=True,
        )
        row = offline_replan_from_item(it)
        rows.append(row)
        with progress.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(
            f"  -> ver={row['verifier']['verdict']} esc={row['semantic_escalated']} "
            f"cat={row['category']} ok={row.get('final_overall_ok')}",
            flush=True,
        )
    return summarize_targeted(rows)


def summarize_targeted(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def slice_rows(kind: str | None = None, label: str | None = None) -> list[dict]:
        out = rows
        if kind:
            out = [r for r in out if r.get("source_kind") == kind]
        if label:
            out = [r for r in out if r.get("label") == label]
        return out

    def metrics(subset: list[dict]) -> dict[str, Any]:
        cats = Counter(r.get("category") for r in subset)
        type_c = [r for r in subset if r.get("label") == "TYPE_C"]
        valid = [r for r in subset if r.get("label") == "VALID_SUCCESS"]
        detected = sum(
            1 for r in type_c if r.get("verifier", {}).get("verdict") == "fail"
        )
        recovered = sum(1 for r in type_c if r.get("category") == "successful_semantic_recovery")
        failed_rec = sum(1 for r in type_c if r.get("category") == "failed_semantic_recovery")
        false_esc = sum(1 for r in valid if r.get("false_semantic_escalation"))
        harmful = sum(1 for r in valid if r.get("category") == "harmful_false_escalation")
        harmless_fp = sum(1 for r in valid if r.get("category") == "false_escalation_harmless")
        return {
            "n": len(subset),
            "type_c_n": len(type_c),
            "valid_n": len(valid),
            "type_c_detected_fail": detected,
            "successful_semantic_recovery": recovered,
            "failed_semantic_recovery": failed_rec,
            "semantic_recovery_rate": round(
                100.0 * recovered / max(detected, 1), 2
            )
            if detected
            else None,
            "false_semantic_escalation": false_esc,
            "harmful_false_escalation": harmful,
            "false_escalation_harmless": harmless_fp,
            "categories": dict(cats),
        }

    return {
        "overall": metrics(rows),
        "historical_real": metrics(
            [r for r in rows if r.get("source_kind") == "historical_real"]
        ),
        "synthetic": metrics(
            [
                r
                for r in rows
                if str(r.get("source_kind", "")).startswith("synthetic")
            ]
        ),
        "rows": rows,
    }


def run_full_live(*, runs: int = 3, results_subdir: str = "full_live") -> dict[str, Any]:
    """Full 19×3 live with experimental semantic escalation wrapper via monkeypatch-style runner.

    Uses a custom run by calling experimental pipeline from a thin adapter.
    """
    from tests.benchmark_multi import runner as runner_mod

    out_dir = OUT / results_subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = SemanticEscalationConfig(
        enable_failure_escalation=True,
        enable_semantic_escalation=True,
        uncertain_policy="escalate",
    )

    # Patch run_case live path to use experimental pipeline
    original_run_case = runner_mod.run_case

    def run_case_semantic(case, **kwargs):  # noqa: ANN001
        if not kwargs.get("live"):
            return original_run_case(case, **kwargs)
        t0 = time.time()
        sources = runner_mod._load_sources(case, kwargs["datasets_dir"])
        und_model = "qwen2.5:7b"
        understanding = runner_mod._build_understanding_live(
            sources,
            base_url=kwargs.get("base_url", "http://localhost:11434"),
            model=und_model,
            chat_json_fn=kwargs.get("chat_json_fn"),
        )
        pipeline = run_integration_pipeline_semantic_experimental(
            case.prompt,
            sources,
            understanding,
            config=cfg,
            max_retries=kwargs.get("max_retries", 2),
            base_url=kwargs.get("base_url", "http://localhost:11434"),
            chat_json_fn=kwargs.get("chat_json_fn"),
        )
        from tests.benchmark_multi.evaluate import evaluate_case as ev

        # Evaluator metadata whitelist is frozen — inject Phase 35 attribution
        # fields after evaluate without changing evaluate.py semantics.
        ev_row = ev(case, pipeline=pipeline, understanding=understanding)
        pmeta = dict(getattr(pipeline, "metadata", None) or {})
        meta = dict(ev_row.get("metadata") or {})
        for k in (
            "semantic_verifier_invoked",
            "semantic_escalation_32b",
            "failure_escalation_32b",
            "escalation_source",
            "semantic_verifier",
            "semantic_escalation",
            "semantic_verifier_elapsed_s",
            "semantic_strong_elapsed_s",
            "final_path",
            "escalated",
            "escalation_reason",
            "final_model",
            "initial_model",
        ):
            if k in pmeta:
                meta[k] = pmeta[k]
        # Failure-only path: ensure escalation_source attribution
        if meta.get("failure_escalation_32b") and not meta.get("semantic_escalation_32b"):
            meta.setdefault("escalation_source", "failure")
        ev_row["metadata"] = meta
        ev_row["elapsed_s"] = round(time.time() - t0, 2)
        return ev_row

    runner_mod.run_case = run_case_semantic  # type: ignore[assignment]
    try:
        run_summaries = []
        for i in range(runs):
            print(f"=== phase35 live run {i+1}/{runs} ===", flush=True)
            summary = run_suite(
                live=True,
                model="qwen2.5:7b",
                save=True,
                max_retries=2,
                results_dir=out_dir,
                model_strategy=None,  # experimental path owns escalation
            )
            run_summaries.append(summary)
            # enrich with semantic meta from cases
            _annotate_run_file(out_dir)
        return aggregate_live(out_dir, run_summaries)
    finally:
        runner_mod.run_case = original_run_case  # type: ignore[assignment]


def _annotate_run_file(out_dir: Path) -> None:
    for p in sorted(out_dir.glob("2026*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        for c in data.get("cases") or []:
            meta = c.get("metadata") or {}
            c["semantic_verifier_invoked"] = bool(meta.get("semantic_verifier_invoked"))
            c["semantic_escalation_32b"] = bool(meta.get("semantic_escalation_32b"))
            c["failure_escalation_32b"] = bool(meta.get("failure_escalation_32b"))
            c["escalation_source"] = meta.get("escalation_source")
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _pctile(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return round(s[0], 2)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return round(s[f], 2)
    return round(s[f] + (s[c] - s[f]) * (k - f), 2)


def aggregate_live(out_dir: Path, run_summaries: list[dict]) -> dict[str, Any]:
    files = sorted(out_dir.glob("2026*.json"))
    cases_all = []
    for p in files:
        data = json.loads(p.read_text(encoding="utf-8"))
        for c in data.get("cases") or []:
            cases_all.append(c)

    n = max(len(cases_all), 1)

    def _mflag(c: dict, key: str) -> bool:
        return bool(c.get(key) or (c.get("metadata") or {}).get(key))

    overall = sum(1 for c in cases_all if c.get("overall_ok")) / n * 100
    safe = sum(1 for c in cases_all if c.get("safe_outcome")) / n * 100
    unsafe = sum(1 for c in cases_all if c.get("unsafe_execution")) / n * 100
    ver_inv = sum(1 for c in cases_all if _mflag(c, "semantic_verifier_invoked")) / n * 100
    sem_32 = sum(1 for c in cases_all if _mflag(c, "semantic_escalation_32b")) / n * 100
    fail_32 = sum(1 for c in cases_all if _mflag(c, "failure_escalation_32b")) / n * 100
    total_32 = (
        sum(
            1
            for c in cases_all
            if _mflag(c, "failure_escalation_32b") or _mflag(c, "semantic_escalation_32b")
        )
        / n
        * 100
    )

    # Latency from suite case timing if present
    lats = []
    ver_lats = []
    strong_lats = []
    for c in cases_all:
        for k in ("elapsed_s", "latency_s", "duration_s"):
            if c.get(k) is not None:
                lats.append(float(c[k]))
                break
        else:
            obs = c.get("observability") or {}
            if obs.get("elapsed_s") is not None:
                lats.append(float(obs["elapsed_s"]))
        meta = c.get("metadata") or {}
        if meta.get("semantic_verifier_elapsed_s") is not None:
            ver_lats.append(float(meta["semantic_verifier_elapsed_s"]))
        if meta.get("semantic_strong_elapsed_s") is not None:
            strong_lats.append(float(meta["semantic_strong_elapsed_s"]))

    ss = [c for c in cases_all if c.get("case_id") == "same_schema_union_001"]
    ss_ok = sum(1 for c in ss if c.get("overall_ok")) / max(len(ss), 1) * 100

    src = Counter(
        (c.get("metadata") or {}).get("escalation_source")
        or (
            "semantic"
            if _mflag(c, "semantic_escalation_32b")
            else ("failure" if _mflag(c, "failure_escalation_32b") else "none")
        )
        for c in cases_all
    )

    def _lat_block(vals: list[float]) -> dict[str, Any] | None:
        if not vals:
            return None
        return {
            "n": len(vals),
            "mean": round(statistics.mean(vals), 2),
            "p50": _pctile(vals, 0.50),
            "p95": _pctile(vals, 0.95),
        }

    latency = _lat_block(lats)
    latency_breakdown = {
        "end_to_end_case": latency,
        "semantic_verifier": _lat_block(ver_lats),
        "semantic_strong_replan": _lat_block(strong_lats),
        "notes": [
            "end_to_end includes understanding + fast path + optional verifier/strong",
            "baseline Phase30 ~34s without verifier/semantic strong",
        ],
    }

    return {
        "n_case_runs": len(cases_all),
        "overall_ok": round(overall, 2),
        "safe_outcome": round(safe, 2),
        "unsafe_execution": round(unsafe, 2),
        "verifier_invocation_pct": round(ver_inv, 2),
        "failure_32b_pct": round(fail_32, 2),
        "semantic_32b_pct": round(sem_32, 2),
        "total_32b_pct": round(total_32, 2),
        "same_schema_overall_ok_pct": round(ss_ok, 2),
        "escalation_source_counts": dict(src),
        "latency": latency,
        "latency_breakdown": latency_breakdown,
        "run_summaries_overall": [s.get("overall", {}) for s in run_summaries],
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--targeted", action="store_true")
    ap.add_argument("--full-live", action="store_true")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "baseline_freeze.json").write_text(
        json.dumps(baseline_freeze(), indent=2), encoding="utf-8"
    )
    if args.targeted:
        result = run_targeted_recovery(limit=args.limit)
        (OUT / "semantic_escalation_targeted.json").write_text(
            json.dumps(
                {k: v for k, v in result.items() if k != "rows"},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (OUT / "semantic_replan_traces.json").write_text(
            json.dumps(result["rows"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        hist = result["historical_real"]
        syn = result["synthetic"]
        (OUT / "historical_recovery.json").write_text(
            json.dumps(hist, indent=2), encoding="utf-8"
        )
        (OUT / "synthetic_recovery.json").write_text(
            json.dumps(syn, indent=2), encoding="utf-8"
        )
        fps = [
            r
            for r in result["rows"]
            if r.get("false_semantic_escalation")
        ]
        (OUT / "false_escalation_analysis.json").write_text(
            json.dumps({"count": len(fps), "traces": fps}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps({k: result[k] for k in ("overall", "historical_real", "synthetic")}, indent=2))
    if args.full_live:
        live = run_full_live(runs=args.runs)
        (OUT / "full_live_semantic_escalation.json").write_text(
            json.dumps(live, indent=2), encoding="utf-8"
        )
        if live.get("latency_breakdown"):
            (OUT / "latency_breakdown.json").write_text(
                json.dumps(live["latency_breakdown"], indent=2), encoding="utf-8"
            )
        (OUT / "escalation_source_breakdown.json").write_text(
            json.dumps(
                {
                    "counts": live.get("escalation_source_counts"),
                    "failure_32b_pct": live.get("failure_32b_pct"),
                    "semantic_32b_pct": live.get("semantic_32b_pct"),
                    "total_32b_pct": live.get("total_32b_pct"),
                    "verifier_invocation_pct": live.get("verifier_invocation_pct"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(json.dumps(live, indent=2))
