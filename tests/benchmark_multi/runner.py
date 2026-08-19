"""Multi-file Integration Pipeline benchmark runner.

Examples:
  python -m tests.benchmark_multi.generate_datasets
  python -m tests.benchmark_multi.runner --deterministic
  python -m tests.benchmark_multi.runner --live --model qwen2.5:7b --runs 3
"""

from __future__ import annotations

import argparse
import traceback
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from core.integrate.integration_pipeline import run_integration_pipeline
from core.integrate.relationship_infer import build_cross_file_understanding
from core.integrate.relationship_types import CrossFileRelationship
from tests.benchmark_multi import DATASETS_DIR, RESULTS_DIR
from tests.benchmark_multi.evaluate import evaluate_case
from tests.benchmark_multi.generate_datasets import ensure_datasets
from tests.benchmark_multi.metrics import save_summary, summarize_multi_run, summarize_results
from tests.benchmark_multi.schema import MultiBenchmarkCase, load_all_cases


def _stem(name: str) -> str:
    return Path(name).stem


def _load_sources(case: MultiBenchmarkCase, datasets_dir: Path) -> dict[str, pd.DataFrame]:
    sources: dict[str, pd.DataFrame] = {}
    for fname in case.files:
        path = datasets_dir / fname
        if not path.is_file():
            ensure_datasets(datasets_dir, force=True)
        sources[_stem(fname)] = pd.read_excel(path)
    return sources


def _fixed_chat(case: MultiBenchmarkCase) -> Callable[..., dict[str, Any]]:
    calls = {"n": 0}

    def _fn(prompt: str, **kwargs):  # noqa: ANN003
        del prompt, kwargs
        calls["n"] += 1
        if calls["n"] == 1 and case.fixed_plan is not None:
            return dict(case.fixed_plan)
        if case.fixed_plan_retry is not None:
            return dict(case.fixed_plan_retry)
        if case.fixed_plan is not None:
            return dict(case.fixed_plan)
        return {
            "status": "cannot_plan",
            "steps": [],
            "final_output": None,
            "reason": "no_fixed_plan",
            "ambiguities": [],
        }

    return _fn


def _build_understanding_deterministic(
    sources: dict[str, pd.DataFrame],
    case: MultiBenchmarkCase,
) -> dict[str, Any]:
    named = list(sources.items())
    und = build_cross_file_understanding(
        named,
        infer_relationships=False,
        chat_json_fn=None,
    )
    data = und.to_dict()
    if case.fixed_relationships:
        data["relationships"] = list(case.fixed_relationships)
    return data


def _build_understanding_live(
    sources: dict[str, pd.DataFrame],
    *,
    base_url: str,
    model: str,
    chat_json_fn: Callable[..., dict[str, Any]] | None,
) -> dict[str, Any]:
    und = build_cross_file_understanding(
        list(sources.items()),
        base_url=base_url,
        model=model,
        chat_json_fn=chat_json_fn,
        infer_relationships=True,
    )
    return und.to_dict()


def run_case(
    case: MultiBenchmarkCase,
    *,
    live: bool,
    datasets_dir: Path,
    base_url: str = "http://localhost:11434",
    model: str = "qwen2.5:7b",
    max_retries: int = 2,
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    sources = _load_sources(case, datasets_dir)
    crashed = False
    err_msg = None
    understanding: dict[str, Any] | None = None
    pipeline = None
    try:
        if live:
            understanding = _build_understanding_live(
                sources,
                base_url=base_url,
                model=model,
                chat_json_fn=chat_json_fn,
            )
            pipeline = run_integration_pipeline(
                case.prompt,
                sources,
                understanding,
                max_retries=max_retries,
                base_url=base_url,
                model=model,
                chat_json_fn=chat_json_fn,
            )
        else:
            if case.live_only and case.fixed_plan is None:
                return {
                    "case_id": case.id,
                    "scenario": case.scenario,
                    "domain": case.domain,
                    "status": "skipped",
                    "overall_ok": True,
                    "safe_outcome": True,
                    "unsafe_execution": False,
                    "skipped": True,
                    "failure_categories": [],
                    "levels": {},
                    "planner_quality": {},
                }
            understanding = _build_understanding_deterministic(sources, case)
            pipeline = run_integration_pipeline(
                case.prompt,
                sources,
                understanding,
                max_retries=max_retries,
                base_url=base_url,
                model="benchmark-fixed",
                chat_json_fn=_fixed_chat(case),
            )
        ev = evaluate_case(case, pipeline=pipeline, understanding=understanding)
        return ev
    except Exception as exc:  # noqa: BLE001
        crashed = True
        err_msg = f"{type(exc).__name__}: {exc}"
        return {
            "case_id": case.id,
            "scenario": case.scenario,
            "domain": case.domain,
            "status": "failed",
            "overall_ok": False,
            "safe_outcome": False,
            "unsafe_execution": False,
            "correct_cannot_plan": False,
            "unnecessary_cannot_plan": False,
            "failure_categories": ["crash"],
            "levels": {"error": err_msg, "traceback": traceback.format_exc()[-2000:]},
            "planner_quality": {},
            "crashed": crashed,
        }


def run_suite(
    *,
    live: bool = False,
    model: str = "qwen2.5:7b",
    base_url: str = "http://localhost:11434",
    save: bool = True,
    max_retries: int = 2,
    case_ids: list[str] | None = None,
    results_dir: Path | None = None,
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ensure_datasets(DATASETS_DIR, force=False)
    cases = load_all_cases()
    if case_ids:
        want = set(case_ids)
        cases = [c for c in cases if c.id in want]
    if live:
        cases = [c for c in cases if not c.deterministic_only]
    else:
        cases = [c for c in cases if c.fixed_plan is not None or not c.live_only]

    results = [
        run_case(
            c,
            live=live,
            datasets_dir=DATASETS_DIR,
            base_url=base_url,
            model=model,
            max_retries=max_retries,
            chat_json_fn=chat_json_fn if live else None,
        )
        for c in cases
    ]
    summary = summarize_results(
        results, mode="live" if live else "deterministic", model=model if live else None
    )
    if save:
        out_dir = results_dir or RESULTS_DIR
        path = save_summary(summary, out_dir)
        summary["saved_to"] = str(path)
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Multi-file Integration Pipeline benchmark")
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--runs", type=int, default=1, help="Live runs for mean/min/max/std")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--case", action="append", default=None)
    parser.add_argument(
        "--results-dir",
        default=None,
        help="Optional directory for saved summaries (Phase 27 model comparison)",
    )
    args = parser.parse_args(argv)

    if not args.live and not args.deterministic:
        args.deterministic = True

    results_dir = Path(args.results_dir) if args.results_dir else None

    if args.deterministic:
        summary = run_suite(
            live=False,
            save=not args.no_save,
            max_retries=args.max_retries,
            case_ids=args.case,
            results_dir=results_dir,
        )
        overall = summary["overall"]
        print(
            f"[deterministic] cases={summary['total_cases']} "
            f"ok={overall['overall_ok_rate']}% "
            f"safe={overall['safe_outcome_rate']}% "
            f"unsafe={overall['unsafe_execution_rate']}%"
        )
        if summary.get("saved_to"):
            print(f"saved: {summary['saved_to']}")

    if args.live:
        runs = []
        for i in range(max(1, args.runs)):
            print(f"=== live run {i + 1}/{args.runs} model={args.model} ===")
            summary = run_suite(
                live=True,
                model=args.model,
                base_url=args.base_url,
                save=not args.no_save,
                max_retries=args.max_retries,
                case_ids=args.case,
                results_dir=results_dir,
            )
            runs.append(summary)
            o = summary["overall"]
            print(
                f"run{i + 1}: ok={o['overall_ok_rate']}% safe={o['safe_outcome_rate']}% "
                f"unsafe={o['unsafe_execution_rate']}% success={o['pipeline_success_rate']}% "
                f"cannot_plan={o['cannot_plan_rate']}%"
            )
        if len(runs) > 1:
            multi = summarize_multi_run(runs)
            out_root = results_dir or RESULTS_DIR
            out_root.mkdir(parents=True, exist_ok=True)
            import json

            multi_path = out_root / "live_3run_summary.json"
            multi_path.write_text(
                json.dumps(multi, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"3-run summary: {multi_path}")
            for k, v in (multi.get("metrics") or {}).items():
                print(f"  {k}: mean={v['mean']} min={v['min']} max={v['max']} std={v['std']}")


if __name__ == "__main__":
    main()
