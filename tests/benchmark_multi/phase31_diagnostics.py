"""Phase 31 — Required Output Contract Diagnostics (offline + optional live probe).

Diagnostic only: does not change production validators, escalation, or route_multi.
Golden labels are used solely for offline probe scoring, never for production gates.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.integration_planner import build_integration_plan
from core.integrate.output_contract_diagnostics import observe_output_contract
from core.integrate.planner_model_strategy import _ESCALATION_TRIGGER_CODES
from core.integrate.relationship_infer import build_cross_file_understanding
from tests.benchmark_multi import DATASETS_DIR
from tests.benchmark_multi.generate_datasets import ensure_datasets
from tests.benchmark_multi.schema import load_all_cases

OUT = Path("benchmark_results/multi/phase31")

PHASE30_LIVE = Path("benchmark_results/multi/phase30/live_grain_hardening")
PHASE29_TRACES = Path("benchmark_results/multi/phase29/silent_failure_traces.json")
PHASE27_7B = Path("benchmark_results/multi/phase27/qwen2.5_7b/full_19")
PHASE27_32B = Path("benchmark_results/multi/phase27/qwen3_32b/full_19")
PHASE28_LIVE = Path("benchmark_results/multi/phase28/live_escalation")

# Diagnostic labels only (benchmark expected structural columns). Not production.
_TYPE_C_CASE = "same_schema_union_001"
_TYPE_B_CASE = "three_file_chain_001"

# Cases for declaration probe (diverse intents + Type-B residual + safety)
_PROBE_CASE_IDS = [
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
    "ambiguous_keys_001",
    "many_to_many_001",
    "incompatible_union_001",
    "unrelated_files_001",
    "impossible_aggregate_001",
]


def _load_run_files(root: Path) -> list[Path]:
    files = sorted(root.glob("2026*.json"))
    return files


def _case_by_id() -> dict[str, Any]:
    return {c.id: c for c in load_all_cases()}


def _structural_expected(case: Any) -> set[str]:
    """Benchmark structural required columns (exclude metric aliases) — diagnostic only."""
    req = list(case.expected.result.required_columns or [])
    metric_aliases = {
        str(m.get("alias"))
        for m in (case.expected.result.expected_metrics or [])
        if m.get("alias")
    }
    for step in (case.fixed_plan or {}).get("steps") or []:
        if step.get("op") != "aggregate":
            continue
        for m in (step.get("params") or {}).get("metrics") or []:
            if isinstance(m, dict) and m.get("alias"):
                metric_aliases.add(str(m["alias"]))
    return {c for c in req if c not in metric_aliases}


def baseline_freeze() -> dict[str, Any]:
    # Prefer Phase 30 summary with escalation KPIs; fall back to runner 3-run summary.
    summary_path = Path("benchmark_results/multi/phase30/live_grain_hardening_summary.json")
    if not summary_path.is_file():
        summary_path = PHASE30_LIVE / "live_3run_summary.json"
    p30 = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = p30.get("metrics") or p30

    def _mean(key: str) -> float:
        block = metrics.get(key)
        if isinstance(block, dict):
            return float(block.get("mean") or 0.0)
        return float(block or 0.0)

    payload = {
        "phase": 31,
        "frozen_from": str(summary_path),
        "overall_ok": _mean("overall_ok_rate"),
        "safe_outcome": _mean("safe_outcome_rate"),
        "unsafe_execution": _mean("unsafe_execution_rate"),
        "escalation_rate": _mean("escalation_rate"),
        "strong_planner_invocation_rate": _mean("strong_planner_invocation_rate"),
        "escalation_success_rate": _mean("escalation_success_rate"),
        "three_file_final": _mean("three_file_final_result_success_rate"),
        "composite_final": _mean("composite_final_result_success_rate"),
        "latency_est_s": 34.14,
        "phase29_type_b_count": 2,
        "phase29_type_c_count": 6,
        "note": "Phase 31 diagnostic must not change these production KPIs.",
    }
    return payload


def build_type_b_traces() -> dict[str, Any]:
    """Reconstruct Type-B residual traces from Phase 29 + Phase 30 live."""
    cases = _case_by_id()
    case = cases[_TYPE_B_CASE]
    structural = sorted(_structural_expected(case))

    p29 = json.loads(PHASE29_TRACES.read_text(encoding="utf-8"))
    type_b = [
        t
        for t in p29.get("traces") or []
        if t.get("phase29_type") == "B_contract_under_specification"
        or t.get("failure_family") == "required_field_set_under_declaration"
    ]

    live_traces: list[dict[str, Any]] = []
    for run_i, path in enumerate(_load_run_files(PHASE30_LIVE), 1):
        data = json.loads(path.read_text(encoding="utf-8"))
        for c in data.get("cases") or []:
            if c.get("case_id") != _TYPE_B_CASE:
                continue
            plan = c.get("plan") or {}
            obs = observe_output_contract(
                plan,
                known_source_ids=["customers", "orders", "products"],
            ).to_dict()
            declared = set(obs.get("declared_required_columns") or [])
            missing_vs_label = sorted(set(structural) - declared)
            live_traces.append(
                {
                    "run": run_i,
                    "status": c.get("status"),
                    "overall_ok": c.get("overall_ok"),
                    "user_prompt": case.prompt,
                    "diagnostic_structural_expected_columns": structural,
                    "planner_declaration": plan.get("final_output_requirements"),
                    "missing_declaration_vs_diagnostic_label": missing_vs_label,
                    "selected_operations": c.get("selected_operations"),
                    "output_contract_diagnostics": obs,
                    "failure_categories": c.get("failure_categories"),
                    "metadata_path": {
                        "final_model": (c.get("metadata") or {}).get("final_model"),
                        "escalated": (c.get("metadata") or {}).get("escalated"),
                        "final_path": (c.get("metadata") or {}).get("final_path"),
                        "escalation_reason": (c.get("metadata") or {}).get(
                            "escalation_reason"
                        ),
                    },
                    "intent_loss_trace": {
                        "user_intent_summary": (
                            "Connect product info to orders; compute order amount "
                            "by customer × category (readable customer identity)."
                        ),
                        "required_semantic_output_components_diagnostic": structural
                        + ["total_amount_or_equivalent_metric"],
                        "planner_declaration": plan.get("final_output_requirements"),
                        "missing_declaration": missing_vs_label,
                        "plan_consequence": (
                            "group_by / required_columns use customer_id (or omit "
                            "customers source); customer_name never required"
                        ),
                        "final_result_consequence": "missing_structural_columns vs benchmark",
                        "first_loss_point": (
                            "Planner final_output_requirements / aggregate group_by "
                            "choose surrogate id over descriptive name field that "
                            "existing required_columns contract can already express"
                        ),
                        "why_validators_passed": (
                            "Validators only check declared required_columns exist; "
                            "they cannot invent undeclared user-facing fields"
                        ),
                        "classification": "existing_contract_omission",
                    },
                }
            )

    return {
        "type_b_case_id_analysis_only": _TYPE_B_CASE,
        "type_b_count_phase29": len(type_b),
        "user_prompt": case.prompt,
        "diagnostic_structural_expected": structural,
        "phase29_traces": type_b,
        "phase30_live_traces": live_traces,
        "classification": {
            "case_a_existing_contract_omission": True,
            "case_b_contract_expressiveness_gap": False,
            "case_c_fundamentally_semantic": False,
            "rationale": (
                "required_columns already can list customer_name; 32B primary "
                "plans declare it and succeed. Gap is planner declaration "
                "reliability, not missing DSL concept."
            ),
        },
    }


def output_contract_audit() -> dict[str, Any]:
    return {
        "contracts": [
            {
                "contract": "required_columns",
                "declared_by_planner": True,
                "validated": True,
                "result_checked": True,
                "expresses": (
                    "Columns that MUST appear on final_output (Planner-declared). "
                    "Plan/Result validators only check presence of the declared set — "
                    "not completeness vs user intent."
                ),
                "planner_prompt": (
                    "Observed names needed in the final answer; for group/summary "
                    "SHOULD include readable entity fields after joins + metrics"
                ),
                "strength": "hard presence check for declared set; weak vs under-declaration",
            },
            {
                "contract": "final grain",
                "declared_by_planner": True,
                "validated": True,
                "result_checked": "observability/info only (Phase 30 blocks row+collapse)",
                "expresses": "What one final row represents (detail|entity|group|summary)",
            },
            {
                "contract": "one_row_represents",
                "declared_by_planner": True,
                "validated": "info only",
                "result_checked": False,
                "expresses": "Free-text self-check phrase; not semantically interpreted",
                "status": "underused — cannot deterministically validate without NLP",
            },
            {
                "contract": "select_columns",
                "declared_by_planner": True,
                "validated": True,
                "result_checked": "via final schema",
                "expresses": "Optional projection; not a required-output declaration",
            },
            {
                "contract": "aggregate group_by / aliases",
                "declared_by_planner": True,
                "validated": True,
                "result_checked": True,
                "expresses": "Collapsed output grain keys + metric names",
            },
            {
                "contract": "join keys / suffixes / source refs",
                "declared_by_planner": True,
                "validated": True,
                "result_checked": "amp/unmatched etc.",
                "expresses": "Structural linkage; not final user-facing field set",
            },
            {
                "contract": "required source contribution",
                "declared_by_planner": False,
                "validated": False,
                "result_checked": False,
                "expresses": "MISSING — which uploaded sources must contribute to final",
                "note": (
                    "Not required to explain Type-B: field under-declaration already "
                    "implies customers may be unused. Adding this risks file-count "
                    "heuristics and over-declaration."
                ),
            },
        ]
    }


def under_declaration_taxonomy() -> dict[str, Any]:
    return {
        "families": [
            {
                "id": "required_field_under_declaration",
                "definition": (
                    "Planner declares a required_columns set that is internally "
                    "consistent with the plan but omits fields the user-facing "
                    "answer needs; existing contract can express the omitted fields."
                ),
                "type_b_evidence": True,
                "example_analysis_only": "customer_id declared; customer_name omitted",
            },
            {
                "id": "required_source_omission_consequence",
                "definition": (
                    "Secondary effect when omitted required fields live only on an "
                    "unused source — plan never references that source."
                ),
                "type_b_evidence": True,
                "primary": False,
                "note": "Consequence of field under-declaration, not a separate DSL gap",
            },
            {
                "id": "declared_collapsed_grain_wrong_user_intent",
                "definition": "Type C — internally consistent group+aggregate wrong intent",
                "type_b_evidence": False,
                "out_of_scope_phase31": True,
                "example_analysis_only": _TYPE_C_CASE,
            },
        ],
        "counts": {
            "existing_contract_omission": 1,
            "missing_contract_concept": 0,
            "fundamentally_semantic": 0,
            "type_b_residual_cases": 1,
        },
    }


def frozen_declaration_reliability() -> dict[str, Any]:
    """7B vs 32B declaration reliability from frozen Phase 27 corpus (no new LLM)."""
    cases = _case_by_id()
    structural = _structural_expected(cases[_TYPE_B_CASE])

    def _score(root: Path, model: str) -> dict[str, Any]:
        runs = []
        for path in _load_run_files(root):
            data = json.loads(path.read_text(encoding="utf-8"))
            for c in data.get("cases") or []:
                if c.get("case_id") != _TYPE_B_CASE:
                    continue
                plan = c.get("plan") or {}
                req = plan.get("final_output_requirements") or {}
                declared = set(req.get("required_columns") or [])
                under = sorted(structural - declared)
                # over vs structural label (extras beyond diagnostic expected)
                over = sorted(declared - structural - {"total_order_amount", "total_amount"})
                runs.append(
                    {
                        "overall_ok": c.get("overall_ok"),
                        "status": c.get("status"),
                        "declared": sorted(declared),
                        "under_vs_label": under,
                        "over_vs_label": over,
                        "includes_customer_name": "customer_name" in declared,
                    }
                )
        n = max(len(runs), 1)
        correct = sum(1 for r in runs if not r["under_vs_label"] and r["status"] == "success")
        under_n = sum(1 for r in runs if r["under_vs_label"])
        over_n = sum(1 for r in runs if r["over_vs_label"])
        return {
            "model": model,
            "n": len(runs),
            "declaration_accuracy_vs_structural_label": round(100.0 * correct / n, 2),
            "under_declaration_rate": round(100.0 * under_n / n, 2),
            "over_declaration_rate": round(100.0 * over_n / n, 2),
            "runs": runs,
        }

    # Valid-control confusion: on overall_ok plans, does required_columns stay non-empty?
    control_stats: dict[str, Any] = {}
    for label, root in (("7b", PHASE27_7B), ("32b", PHASE27_32B)):
        empty = 0
        nonempty = 0
        for path in _load_run_files(root):
            data = json.loads(path.read_text(encoding="utf-8"))
            for c in data.get("cases") or []:
                if not c.get("overall_ok"):
                    continue
                req = (c.get("plan") or {}).get("final_output_requirements") or {}
                cols = req.get("required_columns") or []
                if cols:
                    nonempty += 1
                else:
                    empty += 1
        control_stats[label] = {
            "valid_success_with_required_columns": nonempty,
            "valid_success_empty_required_columns": empty,
        }

    return {
        "method": "frozen_phase27_full_plans",
        "type_b_case": _TYPE_B_CASE,
        "structural_label": sorted(structural),
        "qwen2.5:7b": _score(PHASE27_7B, "qwen2.5:7b"),
        "qwen3:32b": _score(PHASE27_32B, "qwen3:32b"),
        "valid_control_required_columns_presence": control_stats,
        "interpretation": (
            "32B declares customer_name reliably on Type-B case; 7B systematically "
            "under-declares (uses customer_id). Existing required_columns contract "
            "is expressible; reliability differs by model."
        ),
    }


def _understanding_for_case(case: Any) -> dict[str, Any]:
    ensure_datasets(DATASETS_DIR, force=False)
    sources = {Path(f).stem: pd.read_excel(DATASETS_DIR / f) for f in case.files}
    und = build_cross_file_understanding(
        list(sources.items()), infer_relationships=False
    ).to_dict()
    if case.fixed_relationships:
        und["relationships"] = list(case.fixed_relationships)
    return und


def live_declaration_probe(
    *,
    models: list[str] | None = None,
    case_ids: list[str] | None = None,
    runs: int = 1,
    progress_path: Path | None = None,
) -> dict[str, Any]:
    """Optional live probe: full IntegrationPlan via production planner (unchanged prompt).

    Scores declared required_columns against diagnostic structural labels only.
    Does not alter production prompts or validators.
    """
    models = models or ["qwen2.5:7b", "qwen3:32b"]
    case_ids = case_ids or [
        _TYPE_B_CASE,
        "lookup_join_001",
        "composite_key_join_001",
        "dirty_multifile_001",
        "join_aggregate_001",
        "same_schema_union_001",
        "ambiguous_keys_001",
        "master_detail_join_001",
    ]
    cases = _case_by_id()
    results: list[dict[str, Any]] = []
    progress_path = progress_path or (OUT / "live_probe_progress.jsonl")
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text("", encoding="utf-8")

    for model in models:
        for cid in case_ids:
            case = cases.get(cid)
            if case is None:
                continue
            structural = _structural_expected(case)
            und = _understanding_for_case(case)
            for run in range(1, runs + 1):
                print(f"[probe] model={model} case={cid} run={run}", flush=True)
                t0 = time.time()
                plan = build_integration_plan(case.prompt, und, model=model)
                elapsed = round(time.time() - t0, 2)
                req = (
                    plan.final_output_requirements.to_dict()
                    if plan.final_output_requirements
                    and not plan.final_output_requirements.is_empty
                    else None
                )
                declared = set((req or {}).get("required_columns") or [])
                under = sorted(structural - declared) if structural else []
                over = sorted(declared - structural) if structural else sorted(declared)
                if plan.status != "planned":
                    verdict = "cannot_determine"
                elif structural and under:
                    verdict = "under_declaration"
                elif structural and over and not under:
                    verdict = "over_declaration"
                elif structural and not under:
                    verdict = "correct_declaration"
                else:
                    verdict = "no_structural_label"
                row = {
                    "model": model,
                    "case_id": cid,
                    "run": run,
                    "elapsed_s": elapsed,
                    "status": plan.status,
                    "declared": req,
                    "structural_label": sorted(structural),
                    "under": under,
                    "over": over,
                    "verdict": verdict,
                    "ops": [s.op for s in plan.steps],
                }
                results.append(row)
                with progress_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(
                    f"[probe] done verdict={verdict} elapsed={elapsed}s under={under}",
                    flush=True,
                )

    by_model: dict[str, Counter[str]] = {m: Counter() for m in models}
    for r in results:
        by_model[r["model"]][r["verdict"]] += 1

    summary = {}
    for m, ctr in by_model.items():
        total = sum(ctr.values()) or 1
        summary[m] = {
            "n": total,
            "correct_declaration_rate": round(100.0 * ctr["correct_declaration"] / total, 2),
            "under_declaration_rate": round(100.0 * ctr["under_declaration"] / total, 2),
            "over_declaration_rate": round(100.0 * ctr["over_declaration"] / total, 2),
            "cannot_determine_rate": round(100.0 * ctr["cannot_determine"] / total, 2),
            "counts": dict(ctr),
        }

    return {
        "method": "live_full_plan_unchanged_production_prompt",
        "runs_per_case": runs,
        "cases": case_ids,
        "summary": summary,
        "results": results,
    }


def candidate_matrix() -> dict[str, Any]:
    return {
        "candidates": [
            {
                "id": "A_stronger_required_columns_use",
                "existing_or_new": "existing",
                "golden_independent_validation": (
                    "Yes for presence of declared columns; "
                    "No for completeness vs undeclared user intent"
                ),
                "reliability_7b": "low on Type-B (systematic id-vs-name under-declare)",
                "reliability_32b": "high on Type-B (declares customer_name)",
                "under_declare_risk": "high for 7B without declaration improvement",
                "over_declare_risk": "moderate (32B may add region etc.; currently non-blocking)",
                "deterministic_validation": "partial (declared set only)",
                "verdict": "recommended",
                "note": "Prefer Phase 32 planner declaration improvement before new DSL",
            },
            {
                "id": "B_one_row_represents_activation",
                "existing_or_new": "existing_underused",
                "golden_independent_validation": False,
                "reliability_7b": "phrase often present but vague",
                "reliability_32b": "similar",
                "under_declare_risk": "n/a",
                "over_declare_risk": "n/a",
                "deterministic_validation": False,
                "verdict": "reject",
                "note": "Free text; Python cannot verify without semantic inference",
            },
            {
                "id": "C_required_source_contribution",
                "existing_or_new": "new",
                "golden_independent_validation": (
                    "Yes IF planner declares sources; verify lineage inclusion only"
                ),
                "reliability_7b": "unknown / likely under-declare",
                "reliability_32b": "unknown; may over-declare all uploads",
                "under_declare_risk": "high",
                "over_declare_risk": "high (temptation toward all-files-must-contribute)",
                "deterministic_validation": True,
                "verdict": "promising_but_risky",
                "note": "Not necessary for Type-B if required_columns include source-only fields",
            },
            {
                "id": "D_output_projection_contract",
                "existing_or_new": "new_or_duplicate",
                "golden_independent_validation": "duplicates required_columns",
                "reliability_7b": "same failure mode",
                "reliability_32b": "same",
                "under_declare_risk": "same",
                "over_declare_risk": "same",
                "deterministic_validation": True,
                "verdict": "reject",
                "note": "Contract minimality — does not add expressiveness",
            },
            {
                "id": "E_field_survival_contract",
                "existing_or_new": "mostly_existing",
                "golden_independent_validation": True,
                "reliability_7b": "n/a",
                "reliability_32b": "n/a",
                "under_declare_risk": "n/a",
                "over_declare_risk": "n/a",
                "deterministic_validation": True,
                "verdict": "needs_more_evidence",
                "note": (
                    "Already partially covered by required_columns materialization / "
                    "loss_trace. Does not detect undeclared fields."
                ),
            },
        ],
        "golden_independent_candidate_count": 3,
        "deterministically_validatable_candidate_count": 3,
        "recommended_next": "A_stronger_required_columns_use",
    }


def escalation_trigger_audit() -> dict[str, Any]:
    triggers = sorted(_ESCALATION_TRIGGER_CODES)
    return {
        "current_escalation_triggers": triggers,
        "trigger_count": len(triggers),
        "abstraction": (
            "Allowlist of recoverable final-contract / projection failure codes "
            "after fast-path exhaustion. Still evidence-based (failed status + codes), "
            "not scenario/domain routing."
        ),
        "generic_recoverability": True,
        "risk_of_error_code_routing_growth": (
            "Moderate — Phase 30 added final_grain_contradiction as a third code. "
            "If each new validator error gets its own trigger entry, the set becomes "
            "an implicit error-code router. Prefer a future family abstraction "
            "(e.g. recoverable_final_contract_evidence) rather than unbounded growth."
        ),
        "phase31_action": "no new triggers added (diagnostic phase)",
        "future_refactor_recommendation": (
            "Group projection/grain/required-field codes under one recoverability "
            "family flag on ValidationError, keep unsafe/non-escalate sets separate."
        ),
    }


def phase31_kpis(
    *,
    frozen: dict[str, Any],
    live_probe: dict[str, Any] | None,
) -> dict[str, Any]:
    f7 = frozen["qwen2.5:7b"]
    f32 = frozen["qwen3:32b"]
    out = {
        "type_b_count": 1,
        "existing_contract_omission_count": 1,
        "missing_contract_concept_count": 0,
        "fundamentally_semantic_count": 0,
        "candidate_contract_count": 5,
        "7B_declaration_accuracy": f7["declaration_accuracy_vs_structural_label"],
        "7B_under_declaration_rate": f7["under_declaration_rate"],
        "7B_over_declaration_rate": f7["over_declaration_rate"],
        "32B_declaration_accuracy": f32["declaration_accuracy_vs_structural_label"],
        "32B_under_declaration_rate": f32["under_declaration_rate"],
        "32B_over_declaration_rate": f32["over_declaration_rate"],
        "golden_independent_candidate_count": 3,
        "deterministically_validatable_candidate_count": 3,
    }
    if live_probe:
        out["live_probe_summary"] = live_probe.get("summary")
    return out


def write_artifacts(*, run_live_probe: bool = True) -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, Any] = {}

    artifacts["baseline_freeze"] = baseline_freeze()
    artifacts["type_b_failure_traces"] = build_type_b_traces()
    artifacts["output_contract_audit"] = output_contract_audit()
    artifacts["under_declaration_taxonomy"] = under_declaration_taxonomy()
    artifacts["planner_declaration_probe_frozen"] = frozen_declaration_reliability()
    artifacts["candidate_contract_matrix"] = candidate_matrix()
    artifacts["escalation_trigger_audit"] = escalation_trigger_audit()

    live: dict[str, Any] | None = None
    if run_live_probe:
        live = live_declaration_probe(runs=1)
        artifacts["planner_declaration_probe"] = {
            "frozen_corpus": artifacts["planner_declaration_probe_frozen"],
            "live_probe": live,
        }
    else:
        artifacts["planner_declaration_probe"] = {
            "frozen_corpus": artifacts["planner_declaration_probe_frozen"],
            "live_probe": None,
        }

    artifacts["phase31_kpis"] = phase31_kpis(
        frozen=artifacts["planner_declaration_probe_frozen"],
        live_probe=live,
    )

    for name, payload in artifacts.items():
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Alias expected names from phase brief
    (OUT / "planner_declaration_probe.json").write_text(
        json.dumps(artifacts["planner_declaration_probe"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return artifacts


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--no-live-probe", action="store_true")
    ap.add_argument(
        "--focused-live",
        action="store_true",
        help="Smaller live probe: Type-B + few controls (7B all, 32B Type-B only)",
    )
    args = ap.parse_args()
    if args.no_live_probe:
        arts = write_artifacts(run_live_probe=False)
    elif args.focused_live:
        OUT.mkdir(parents=True, exist_ok=True)
        arts = write_artifacts(run_live_probe=False)
        # 7B: Type-B + controls; 32B: Type-B only (latency)
        live_7b = live_declaration_probe(
            models=["qwen2.5:7b"],
            case_ids=[
                _TYPE_B_CASE,
                "lookup_join_001",
                "join_aggregate_001",
                "master_detail_join_001",
                "dirty_multifile_001",
                "same_schema_union_001",
                "ambiguous_keys_001",
            ],
            runs=1,
        )
        live_32b = live_declaration_probe(
            models=["qwen3:32b"],
            case_ids=[_TYPE_B_CASE, "join_aggregate_001", "master_detail_join_001"],
            runs=1,
        )
        live = {
            "method": "focused_live_probe",
            "summary": {
                **{f"7b::{k}": v for k, v in live_7b["summary"].items()},
                **{f"32b::{k}": v for k, v in live_32b["summary"].items()},
            },
            "qwen2.5:7b": live_7b,
            "qwen3:32b": live_32b,
        }
        arts["planner_declaration_probe"] = {
            "frozen_corpus": arts["planner_declaration_probe_frozen"],
            "live_probe": live,
        }
        arts["phase31_kpis"] = phase31_kpis(
            frozen=arts["planner_declaration_probe_frozen"],
            live_probe=live,
        )
        (OUT / "planner_declaration_probe.json").write_text(
            json.dumps(arts["planner_declaration_probe"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (OUT / "phase31_kpis.json").write_text(
            json.dumps(arts["phase31_kpis"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        arts = write_artifacts(run_live_probe=True)
    print(json.dumps(arts["phase31_kpis"], ensure_ascii=False, indent=2))
