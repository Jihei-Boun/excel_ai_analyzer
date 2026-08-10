"""Benchmark runner — deterministic (CI) and live (Ollama) modes.

Examples:
  python -m tests.benchmark.generate_datasets
  python -m tests.benchmark.runner --deterministic
  python -m tests.benchmark.runner --live --model qwen2.5:14b
  python -m tests.benchmark.runner --compare benchmark_results/latest.json
"""

from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from tests.benchmark import DATASETS_DIR, RESULTS_DIR
from tests.benchmark.evaluate import (
    classify_failure,
    eval_execution,
    eval_interpretation_grounding,
    eval_plan,
    eval_routing,
    observe_route,
)
from tests.benchmark.generate_datasets import ensure_datasets
from tests.benchmark.metrics import CaseResult, LevelScores, compare_summaries, save_summary, summarize_results
from tests.benchmark.schema import BenchmarkCase, load_all_cases


def _load_dataset(name: str, datasets_dir: Path) -> pd.DataFrame:
    path = datasets_dir / name
    if not path.is_file():
        ensure_datasets(datasets_dir, force=False)
    if not path.is_file():
        raise FileNotFoundError(path)
    return pd.read_excel(path)


def _is_system_question(question: str) -> bool:
    from core.routing.prompt_intent import is_system_data_command

    return is_system_data_command(question)


def _run_system(case: BenchmarkCase, df: pd.DataFrame) -> tuple[Any, str, dict]:
    from core.summary.file_summary import build_file_summary, is_summary_request
    from core.schema.schema_compare import build_schema_outcome, is_schema_request
    from core.filter.value_filter import build_missing_rows_outcome, is_missing_rows_request
    from core.schema.quality import build_quality_outcome, is_quality_request

    if is_summary_request(case.question):
        return None, build_file_summary(df, profile_name=case.profile), {"aggregation": {"operation": "system"}}
    if is_missing_rows_request(case.question):
        reply, table = build_missing_rows_outcome(df, label="current", profile_name=case.profile)
        return table, reply, {"aggregation": {"operation": "system"}}
    if is_quality_request(case.question):
        reply, table = build_quality_outcome([("current", df)], unit_label="파일", prompt=case.question)
        return table, reply, {"aggregation": {"operation": "system"}}
    if is_schema_request(case.question):
        reply, table = build_schema_outcome(
            case.question, [("current", df)], unit_label="파일", profile_name=case.profile
        )
        return table, reply, {"aggregation": {"operation": "system"}}
    return None, "", {"aggregation": {"operation": "system"}}


def _make_fixed_chat_json(case: BenchmarkCase) -> Callable[..., dict[str, Any]]:
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
        return {"steps": []}

    return _fn


def run_case_deterministic(
    case: BenchmarkCase,
    *,
    datasets_dir: Path,
) -> CaseResult:
    df = _load_dataset(case.dataset, datasets_dir)
    levels = LevelScores()
    details: dict[str, Any] = {}
    route_observed = "unknown"
    plan_dict = None
    result_df = None
    reply = ""
    meta: dict[str, Any] = {}
    crashed = False
    err = None
    plan_hint = "none"
    exec_hint = "none"
    analysis_plan_direct = False
    legacy_fallback = False
    pandasai_fallback = False
    first_plan_success = False
    retry_success = False
    retry_exhausted = False
    semantic_warning = False

    try:
        if case.expected.route == "system" or (
            not case.expected.route and _is_system_question(case.question)
        ):
            result_df, reply, meta = _run_system(case, df)
            route_observed = "system"
        elif case.fixed_plan is not None:
            from core.analysis.analysis_pipeline import try_analysis_pipeline

            planned = try_analysis_pipeline(
                case.question,
                df,
                base_url="http://localhost:11434",
                model="benchmark-fixed",
                profile_name=case.profile,
                chat_json_fn=_make_fixed_chat_json(case),
                chat_text_fn=lambda *_a, **_k: "해석 생략",
            )
            if planned is None:
                route_observed = "unknown"
                retry_exhausted = True
                details["pipeline"] = "exhausted"
                retry_log = []
            else:
                result_df = planned.dataframe
                reply = planned.reply
                meta = dict(planned.meta)
                plan_dict = planned.plan.to_dict() if planned.plan else None
                route_observed = observe_route(meta)
                analysis_plan_direct = route_observed == "analysis_plan"
                retry_log = meta.get("retry_log") or []
                if not retry_log:
                    first_plan_success = True
                else:
                    retry_success = True
            semantic_warning = any(
                "semantic" in str(x).lower()
                for x in (meta.get("validation_warnings") or [])
            ) or any(
                r.get("failure_stage") == "semantic_soft_retry" for r in retry_log
            )
        else:
            # routing-only / expectation-only without fixed plan: classify intent
            from core.routing.prompt_intent import is_analytical_request, is_system_data_command

            if is_system_data_command(case.question):
                route_observed = "system"
            elif is_analytical_request(case.question):
                route_observed = "analysis_plan"  # intended route (not executed)
                details["note"] = "routing intent only (no fixed_plan)"
            else:
                route_observed = "unknown"
    except Exception as exc:  # noqa: BLE001
        crashed = True
        err = f"{type(exc).__name__}: {exc}"
        details["traceback"] = traceback.format_exc()
        route_observed = "crash"

    routing_ok, routing_detail = eval_routing(case.expected, route_observed)
    levels.routing = routing_ok
    details["routing"] = routing_detail

    plan_ok, plan_detail, plan_hint = eval_plan(case.expected, plan_dict)
    levels.plan = plan_ok
    details["plan"] = plan_detail
    details["plan_dict"] = plan_dict

    exec_ok, exec_detail, exec_hint = eval_execution(case.expected, result_df)
    levels.execution = exec_ok
    details["execution"] = exec_detail
    details["fallback_reason"] = meta.get("fallback_reason") or meta.get("prior_pipeline_reason")
    details["prior_pipeline_reason"] = meta.get("prior_pipeline_reason")
    details["validation_warnings"] = meta.get("validation_warnings") or []
    details["retry_log"] = meta.get("retry_log") or []
    # Phase 11 recovery observability (from analysis_pipeline meta)
    for _k in (
        "repair_retry_success",
        "regenerate_retry_success",
        "semantic_ambiguity",
        "last_retry_mode",
        "retry_modes",
    ):
        if _k in meta:
            details[_k] = meta[_k]

    interp_ok = None
    if case.expected.interpreter_grounding:
        interp_ok, interp_detail = eval_interpretation_grounding(reply, result_df)
        levels.interpretation = interp_ok
        details["interpretation"] = interp_detail

    if case.expected.expect_safe_failure:
        ok = not crashed and (routing_ok is not False)
    elif case.expected.expect_plan_validation_error:
        ok = plan_ok is False or retry_exhausted or retry_success
    else:
        checks = [x for x in (routing_ok, plan_ok, exec_ok, interp_ok) if x is not None]
        ok = (not crashed) and (all(checks) if checks else route_observed != "crash")

    # Prefer AnalysisPlan success signal
    if analysis_plan_direct and exec_ok is not False and routing_ok is not False:
        pass

    failure_category = classify_failure(
        case=case,
        routing_ok=routing_ok,
        plan_ok=plan_ok,
        exec_ok=exec_ok,
        interp_ok=interp_ok,
        plan_hint=plan_hint,
        exec_hint=exec_hint,
        crashed=crashed,
        route_observed=route_observed,
    )
    if case.expected.expect_safe_failure and not crashed:
        failure_category = "safe_failure_ok"
        ok = True

    return CaseResult(
        case_id=case.id,
        domain=case.domain,
        profile=case.profile,
        question=case.question,
        mode="deterministic",
        ok=ok,
        levels=levels,
        route_observed=route_observed,
        failure_category=failure_category if not ok else (
            "safe_failure_ok" if case.expected.expect_safe_failure else "none"
        ),
        analysis_plan_direct=analysis_plan_direct,
        legacy_fallback=legacy_fallback,
        pandasai_fallback=pandasai_fallback,
        first_plan_success=first_plan_success,
        retry_success=retry_success,
        retry_exhausted=retry_exhausted,
        semantic_warning=semantic_warning,
        details=details,
        error=err,
    )


def run_case_live(
    case: BenchmarkCase,
    *,
    datasets_dir: Path,
    base_url: str,
    model: str,
) -> CaseResult:
    df = _load_dataset(case.dataset, datasets_dir)
    levels = LevelScores()
    details: dict[str, Any] = {}
    crashed = False
    err = None
    plan_dict = None
    result_df = None
    reply = ""
    meta: dict[str, Any] = {}
    route_observed = "unknown"
    analysis_plan_direct = False
    legacy_fallback = False
    pandasai_fallback = False
    first_plan_success = False
    retry_success = False
    retry_exhausted = False
    semantic_warning = False
    plan_hint = "none"
    exec_hint = "none"

    try:
        if case.expected.route == "system" or (
            not case.expected.route and _is_system_question(case.question)
        ):
            result_df, reply, meta = _run_system(case, df)
            route_observed = "system"
        else:
            from core.analysis.analyzer import run_analysis

            # Monkeypatch chat to mark pandasai without requiring live PandasAI server
            # when pipeline succeeds; if pipeline fails, allow real/fallback path.
            result_df, reply, meta = run_analysis(
                df,
                case.question,
                base_url=base_url,
                model=model,
                profile_name=case.profile,
                skip_aggregate_shortcuts=False,
            )
            meta = dict(meta or {})
            route_observed = observe_route(meta)
            op = str((meta.get("aggregation") or {}).get("operation") or "")
            analysis_plan_direct = op == "analysis_plan"
            legacy_fallback = op in {"legacy_simple_groupby_fallback", "groupby"}
            pandasai_fallback = meta.get("source") == "pandasai" or (
                route_observed == "unknown" and not analysis_plan_direct and not legacy_fallback
                and result_df is not None and op not in {"value_match", "list_seed", "system"}
            )
            if op in {"value_match", "list_seed"}:
                route_observed = "retrieval"
            plan_dict = meta.get("analysis_plan")
            retry_log = meta.get("retry_log") or []
            if analysis_plan_direct:
                if not retry_log:
                    first_plan_success = True
                else:
                    retry_success = True
            elif case.expected.route == "analysis_plan" and not analysis_plan_direct:
                # likely exhausted then fallback
                if legacy_fallback or pandasai_fallback or route_observed in {"legacy_fallback", "pandasai", "retrieval"}:
                    retry_exhausted = True
            semantic_warning = any(
                "semantic" in str(x).lower() for x in (meta.get("validation_warnings") or [])
            ) or any(r.get("failure_stage") == "semantic_soft_retry" for r in retry_log)
    except Exception as exc:  # noqa: BLE001
        crashed = True
        err = f"{type(exc).__name__}: {exc}"
        details["traceback"] = traceback.format_exc()
        route_observed = "crash"

    routing_ok, routing_detail = eval_routing(case.expected, route_observed)
    levels.routing = routing_ok
    details["routing"] = routing_detail

    plan_ok, plan_detail, plan_hint = eval_plan(case.expected, plan_dict)
    levels.plan = plan_ok
    details["plan"] = plan_detail

    exec_ok, exec_detail, exec_hint = eval_execution(case.expected, result_df if isinstance(result_df, pd.DataFrame) else None)
    levels.execution = exec_ok
    details["execution"] = exec_detail
    details["fallback_reason"] = meta.get("fallback_reason") or meta.get("prior_pipeline_reason")
    details["prior_pipeline_reason"] = meta.get("prior_pipeline_reason")
    details["validation_warnings"] = meta.get("validation_warnings") or []
    details["retry_log"] = meta.get("retry_log") or []
    for _k in (
        "repair_retry_success",
        "regenerate_retry_success",
        "semantic_ambiguity",
        "last_retry_mode",
        "retry_modes",
    ):
        if _k in meta:
            details[_k] = meta[_k]
    if isinstance(result_df, pd.DataFrame):
        details["result_preview"] = result_df.head(10).to_dict(orient="records")

    interp_ok = None
    if case.expected.interpreter_grounding:
        interp_ok, interp_detail = eval_interpretation_grounding(reply, result_df if isinstance(result_df, pd.DataFrame) else None)
        levels.interpretation = interp_ok
        details["interpretation"] = interp_detail

    if case.expected.expect_safe_failure:
        ok = not crashed
    else:
        checks = [x for x in (routing_ok, plan_ok, exec_ok, interp_ok) if x is not None]
        ok = (not crashed) and (all(checks) if checks else False)

    failure_category = classify_failure(
        case=case,
        routing_ok=routing_ok,
        plan_ok=plan_ok,
        exec_ok=exec_ok,
        interp_ok=interp_ok,
        plan_hint=plan_hint,
        exec_hint=exec_hint,
        crashed=crashed,
        route_observed=route_observed,
    )
    if case.expected.expect_safe_failure and not crashed:
        failure_category = "safe_failure_ok"
        ok = True
    if (legacy_fallback or pandasai_fallback) and case.expected.route == "analysis_plan" and not ok:
        failure_category = "fallback"

    return CaseResult(
        case_id=case.id,
        domain=case.domain,
        profile=case.profile,
        question=case.question,
        mode="live",
        ok=ok,
        levels=levels,
        route_observed=route_observed,
        failure_category=failure_category if not ok else (
            "safe_failure_ok" if case.expected.expect_safe_failure else "none"
        ),
        analysis_plan_direct=analysis_plan_direct,
        legacy_fallback=legacy_fallback,
        pandasai_fallback=pandasai_fallback,
        first_plan_success=first_plan_success,
        retry_success=retry_success,
        retry_exhausted=retry_exhausted,
        semantic_warning=semantic_warning,
        details=details,
        error=err,
    )


def run_suite(
    *,
    live: bool = False,
    domains: list[str] | None = None,
    base_url: str = "http://localhost:11434",
    model: str = "qwen2.5:14b",
    datasets_dir: Path | None = None,
    save: bool = True,
) -> dict[str, Any]:
    datasets_dir = datasets_dir or DATASETS_DIR
    ensure_datasets(datasets_dir, force=False)
    cases = load_all_cases(domains=domains)
    results: list[CaseResult] = []
    for case in cases:
        if live:
            if case.live_only or True:
                results.append(
                    run_case_live(
                        case,
                        datasets_dir=datasets_dir,
                        base_url=base_url,
                        model=model,
                    )
                )
        else:
            if case.live_only and case.fixed_plan is None and case.expected.route != "system":
                # skip pure live cases in deterministic mode
                continue
            results.append(run_case_deterministic(case, datasets_dir=datasets_dir))

    summary = summarize_results(
        results,
        model=model if live else "fixed_plan",
        mode="live" if live else "deterministic",
    )
    if save:
        path = save_summary(summary, RESULTS_DIR)
        summary["saved_to"] = str(path)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Excel AI Analyzer Phase 6 benchmark")
    parser.add_argument("--deterministic", action="store_true", help="Run CI deterministic suite")
    parser.add_argument("--live", action="store_true", help="Run live Ollama suite")
    parser.add_argument("--domain", action="append", default=None, help="Filter domain (repeatable)")
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--compare", default=None, help="Compare against a previous summary JSON")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    if args.compare and not (args.deterministic or args.live):
        # compare latest vs given
        current_path = RESULTS_DIR / "latest.json"
        if not current_path.is_file():
            print("No latest.json — run a suite first.")
            return 1
        baseline = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        current = json.loads(current_path.read_text(encoding="utf-8"))
        print(json.dumps(compare_summaries(baseline, current), ensure_ascii=False, indent=2))
        return 0

    if not args.deterministic and not args.live:
        args.deterministic = True

    summary = run_suite(
        live=bool(args.live),
        domains=args.domain,
        base_url=args.base_url,
        model=args.model,
        save=not args.no_save,
    )
    overall = summary.get("overall") or {}
    print(
        json.dumps(
            {
                "mode": summary.get("mode"),
                "saved_to": summary.get("saved_to"),
                "total_cases": summary.get("total_cases"),
                "overall_ok_rate": overall.get("overall_ok_rate"),
                "analysis_plan_direct_rate": overall.get("analysis_plan_direct_rate"),
                "fallback_rate": overall.get("fallback_rate"),
                "pandasai_fallback_rate": overall.get("pandasai_fallback_rate"),
                "by_domain": {
                    k: {
                        "result_accuracy": v.get("result_accuracy"),
                        "analysis_plan_direct_rate": v.get("analysis_plan_direct_rate"),
                        "fallback_rate": v.get("fallback_rate"),
                    }
                    for k, v in (summary.get("by_domain") or {}).items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if args.compare:
        baseline = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        print("\n# compare")
        print(json.dumps(compare_summaries(baseline, summary), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
