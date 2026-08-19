"""Phase 27 — model capability comparison harness (no production semantic changes).

Usage:
  python -m tests.benchmark_multi.phase27_compare --models qwen2.5:7b,qwen3:8b,qwen3:32b
  python -m tests.benchmark_multi.phase27_compare --residual-only --runs 3
  python -m tests.benchmark_multi.phase27_compare --full --runs 3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.llm_client import chat_json
from tests.benchmark_multi import RESULTS_DIR
from tests.benchmark_multi.metrics import summarize_multi_run, summarize_results
from tests.benchmark_multi.runner import run_suite

PHASE27_ROOT = RESULTS_DIR / "phase27"

# Fair-comparison constants (must match Phase 26 live)
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MAX_RETRIES = 2
DEFAULT_RUNS = 3

RESIDUAL_CASES = [
    "composite_key_join_001",
    "lookup_join_001",
    "three_file_chain_001",
    "dirty_multifile_001",
]
CONTROL_CASES = [
    "master_detail_join_001",
    "same_schema_union_001",
    "ambiguous_keys_001",
    "unrelated_files_001",
    "many_to_many_001",
]

COMPARE_KPI_KEYS = [
    "overall_ok_rate",
    "safe_outcome_rate",
    "unsafe_execution_rate",
    "pipeline_success_rate",
    "first_plan_success_rate",
    "retry_success_rate",
    "retry_exhausted_rate",
    "cannot_plan_rate",
    "unnecessary_cannot_plan_rate",
    "true_wrong_result_rate",
    "representation_only_mismatch_rate",
    "grain_mismatch_rate",
    "structural_result_mismatch_rate",
    "final_requirement_declared_rate",
    "final_requirement_grain_accuracy",
    "final_requirement_column_recall",
    "requirement_understanding_failure_rate",
    "requirement_preservation_failure_rate",
    "final_projection_failure_rate",
    "required_field_loss_rate",
    "unnecessary_transformation_rate",
    "final_contract_retry_success_rate",
    "repeated_final_contract_failure_rate",
    "composite_key_selection_success_rate",
    "composite_join_success_rate",
    "composite_final_result_success_rate",
    "lookup_final_result_success_rate",
    "three_file_join_chain_success_rate",
    "three_file_final_result_success_rate",
    "dirty_final_result_success_rate",
    "unrelated_safe_outcome_rate",
    "validator_false_positive_rate",
]


def model_slug(model: str) -> str:
    return model.replace(":", "_").replace("/", "_")


def make_timed_chat(
    *,
    model: str,
    base_url: str,
    latencies: list[float],
) -> Callable[..., dict[str, Any]]:
    """Wrap chat_json with latency capture (harness-only; identical calls)."""

    def _fn(
        prompt: str,
        *,
        system: str,
        base_url: str = base_url,
        model: str = model,
        timeout: int = 300,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        t0 = time.perf_counter()
        try:
            return chat_json(
                prompt,
                system=system,
                base_url=base_url,
                model=model,
                timeout=timeout,
            )
        finally:
            latencies.append(time.perf_counter() - t0)

    return _fn


def _mmms(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "std": 0.0}
    return {
        "mean": round(statistics.mean(vals), 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "std": round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0,
    }


def latency_stats(latencies: list[float]) -> dict[str, Any]:
    if not latencies:
        return {"n": 0, "mean": None, "p50": None, "p95": None}
    s = sorted(latencies)
    n = len(s)

    def pct(p: float) -> float:
        idx = min(n - 1, max(0, int(round((p / 100.0) * (n - 1)))))
        return round(s[idx], 3)

    return {
        "n": n,
        "mean": round(statistics.mean(s), 3),
        "p50": pct(50),
        "p95": pct(95),
        "min": round(s[0], 3),
        "max": round(s[-1], 3),
    }


def run_model_suite(
    model: str,
    *,
    case_ids: list[str] | None,
    runs: int,
    base_url: str,
    max_retries: int,
    tag: str,
) -> dict[str, Any]:
    slug = model_slug(model)
    out_dir = PHASE27_ROOT / slug / tag
    out_dir.mkdir(parents=True, exist_ok=True)

    run_summaries: list[dict[str, Any]] = []
    all_latencies: list[float] = []
    wall_times: list[float] = []

    for i in range(runs):
        latencies: list[float] = []
        chat_fn = make_timed_chat(model=model, base_url=base_url, latencies=latencies)
        t0 = time.perf_counter()
        print(f"[phase27] model={model} tag={tag} run={i + 1}/{runs}")
        summary = run_suite(
            live=True,
            model=model,
            base_url=base_url,
            save=True,
            max_retries=max_retries,
            case_ids=case_ids,
            results_dir=out_dir,
            chat_json_fn=chat_fn,
        )
        wall = time.perf_counter() - t0
        wall_times.append(wall)
        all_latencies.extend(latencies)
        summary["phase27"] = {
            "model": model,
            "tag": tag,
            "run_index": i + 1,
            "wall_seconds": round(wall, 3),
            "planner_call_latency": latency_stats(latencies),
        }
        # rewrite saved file with phase27 metadata
        saved = summary.get("saved_to")
        if saved:
            path = Path(saved)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["phase27"] = summary["phase27"]
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            # also copy as runN.json
            (out_dir / f"run{i + 1}.json").write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8"
            )
        run_summaries.append(summary)
        o = summary.get("overall") or {}
        print(
            f"  ok={o.get('overall_ok_rate')} safe={o.get('safe_outcome_rate')} "
            f"unsafe={o.get('unsafe_execution_rate')} wall={wall:.1f}s "
            f"llm_calls={len(latencies)}"
        )

    # Aggregate KPIs across runs from overall dicts
    metrics: dict[str, Any] = {}
    for k in COMPARE_KPI_KEYS:
        vals = []
        for s in run_summaries:
            v = (s.get("overall") or {}).get(k)
            if v is not None:
                vals.append(float(v))
        metrics[k] = _mmms(vals)

    # Scenario deep dive from last run cases
    last_cases = (run_summaries[-1].get("cases") if run_summaries else []) or []
    scenario_probe = _scenario_probe(last_cases)

    multi = summarize_multi_run(run_summaries)
    result = {
        "model": model,
        "slug": slug,
        "tag": tag,
        "runs": runs,
        "case_ids": case_ids,
        "fair_comparison": {
            "base_url": base_url,
            "max_retries": max_retries,
            "temperature": 0,
            "format_json": True,
            "pipeline": "phase26_frozen",
        },
        "metrics": metrics,
        "live_3run_core": multi.get("metrics"),
        "latency": {
            "planner_call": latency_stats(all_latencies),
            "suite_wall_seconds": _mmms(wall_times),
        },
        "scenario_probe_last_run": scenario_probe,
        "saved_runs": [s.get("saved_to") for s in run_summaries],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _scenario_probe(cases: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for c in cases:
        cid = c.get("case_id")
        obs = c.get("observability") or {}
        plan = c.get("plan") or {}
        req = plan.get("final_output_requirements") or {}
        ops = c.get("selected_operations") or []
        out[str(cid)] = {
            "status": c.get("status"),
            "overall_ok": c.get("overall_ok"),
            "safe_outcome": c.get("safe_outcome"),
            "unsafe_execution": c.get("unsafe_execution"),
            "ops": ops,
            "step_count": len(ops),
            "has_aggregate": "aggregate" in ops,
            "has_select": "select_columns" in ops,
            "declared_grain": req.get("grain") or obs.get("declared_final_grain"),
            "one_row_represents": req.get("one_row_represents") or obs.get("one_row_represents"),
            "declared_columns": req.get("required_columns")
            or obs.get("declared_required_columns"),
            "understanding_failure": obs.get("requirement_understanding_failure"),
            "preservation_failure": obs.get("requirement_preservation_failure"),
            "composite_key": c.get("composite_key_selection_success"),
            "composite_final": c.get("composite_final_result_success"),
            "three_file_join": c.get("three_file_join_chain_success"),
            "three_file_final": c.get("three_file_final_result_success"),
            "retry_log_len": len(c.get("retry_log") or []),
            "failure_categories": c.get("failure_categories"),
        }
    return out


def build_comparison_table(model_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    table: dict[str, dict[str, Any]] = {}
    for s in model_summaries:
        model = s["model"]
        table[model] = {
            k: (s.get("metrics") or {}).get(k, {}).get("mean") for k in COMPARE_KPI_KEYS
        }
        table[model]["latency_mean_s"] = ((s.get("latency") or {}).get("planner_call") or {}).get(
            "mean"
        )
        table[model]["wall_mean_s"] = ((s.get("latency") or {}).get("suite_wall_seconds") or {}).get(
            "mean"
        )
    return table


def write_baseline_freeze(path: Path) -> dict[str, Any]:
    from core.integrate import integration_planner

    planner_src = Path(integration_planner.__file__).read_bytes()
    freeze = {
        "phase": 27,
        "purpose": "model_capability_diagnostic",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_head(),
        "planner_system_sha256": hashlib.sha256(
            getattr(integration_planner, "_PLANNER_SYSTEM", "").encode("utf-8")
        ).hexdigest(),
        "planner_module_sha256": hashlib.sha256(planner_src).hexdigest(),
        "contract_files_sha256": {
            rel: _sha256(Path("core/integrate") / rel)
            for rel in (
                "integration_plan_types.py",
                "integration_plan_validate.py",
                "integration_pipeline.py",
                "integration_result_validate.py",
                "integration_contracts.py",
            )
        },
        "fair_comparison": {
            "temperature": 0,
            "format_json": True,
            "max_retries": DEFAULT_MAX_RETRIES,
            "base_url": DEFAULT_BASE_URL,
            "timeout_s": 300,
            "cases_live": 19,
            "runs": DEFAULT_RUNS,
        },
        "phase26_baseline_kpi": {
            "overall_ok": 73.68,
            "safe_outcome": 89.47,
            "unsafe_execution": 0.0,
            "composite_final": 0.0,
            "lookup_final": 0.0,
            "three_file_final": 0.0,
            "dirty_final": 100.0,
            "retry_exhausted": 21.05,
            "final_contract_retry_success": 5.26,
        },
        "note": "Production Planner/Validator/Executor semantics must remain unchanged during Phase 27.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(freeze, ensure_ascii=False, indent=2), encoding="utf-8")
    return freeze


def _git_head() -> str | None:
    import subprocess

    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path.cwd())
            .decode()
            .strip()
        )
    except Exception:  # noqa: BLE001
        return None


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Phase 27 model capability comparison")
    parser.add_argument(
        "--models",
        default="qwen2.5:7b,qwen3:8b,qwen3:32b",
        help="Comma-separated Ollama model names",
    )
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    parser.add_argument("--residual-only", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--write-freeze-only", action="store_true")
    args = parser.parse_args(argv)

    freeze_path = PHASE27_ROOT / "baseline_freeze.json"
    freeze = write_baseline_freeze(freeze_path)
    print(f"baseline freeze: {freeze_path} commit={freeze.get('git_commit')}")
    if args.write_freeze_only:
        return

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.residual_only or not args.full:
        case_ids = RESIDUAL_CASES + CONTROL_CASES
        tag = "residual_probe"
    else:
        case_ids = None
        tag = "full_19"

    if args.full:
        case_ids = None
        tag = "full_19"

    summaries = []
    for model in models:
        s = run_model_suite(
            model,
            case_ids=case_ids,
            runs=args.runs,
            base_url=args.base_url,
            max_retries=args.max_retries,
            tag=tag,
        )
        summaries.append(s)

    comparison = {
        "phase": 27,
        "tag": tag,
        "models": [s["model"] for s in summaries],
        "table": build_comparison_table(summaries),
        "per_model": summaries,
        "baseline_freeze": str(freeze_path),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    out = PHASE27_ROOT / f"model_comparison_{tag}.json"
    out.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"comparison written: {out}")
    # compact table print
    print("\n=== comparison (means) ===")
    header = ["KPI"] + [s["model"] for s in summaries]
    print(" | ".join(header))
    for k in [
        "overall_ok_rate",
        "safe_outcome_rate",
        "unsafe_execution_rate",
        "composite_final_result_success_rate",
        "lookup_final_result_success_rate",
        "three_file_final_result_success_rate",
        "dirty_final_result_success_rate",
        "requirement_understanding_failure_rate",
        "requirement_preservation_failure_rate",
        "retry_exhausted_rate",
        "final_contract_retry_success_rate",
    ]:
        row = [k] + [str(((s.get("metrics") or {}).get(k) or {}).get("mean")) for s in summaries]
        print(" | ".join(row))


if __name__ == "__main__":
    main()
