"""Phase 29 diagnostic harness — offline only; does not change production outcomes."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from core.integrate.result_diagnostics import observe_plan_diagnostics

PHASE27_7B = Path("benchmark_results/multi/phase27/qwen2.5_7b/full_19")
PHASE28_LIVE = Path("benchmark_results/multi/phase28/live_escalation")
PHASE27_32B = Path("benchmark_results/multi/phase27/qwen3_32b/full_19")
OUT = Path("benchmark_results/multi/phase29")


def _load_runs(root: Path) -> list[dict[str, Any]]:
    files = sorted(p for p in root.glob("2026*.json"))
    if not files:
        files = [root / f"run{i}.json" for i in (1, 2, 3) if (root / f"run{i}.json").is_file()]
    return [json.loads(p.read_text(encoding="utf-8")) for p in files]


def _is_silent(c: dict[str, Any]) -> bool:
    return (
        c.get("status") == "success"
        and not bool(c.get("overall_ok"))
        and not bool(c.get("unsafe_execution"))
    )


def _is_valid_success(c: dict[str, Any]) -> bool:
    return c.get("status") == "success" and bool(c.get("overall_ok"))


def build_trace(c: dict[str, Any], *, source: str, run: int) -> dict[str, Any]:
    plan = c.get("plan") or {}
    obs = c.get("observability") or {}
    meta = c.get("metadata") or {}
    diag = observe_plan_diagnostics(
        plan,
        execution_meta=meta,
        final_shape=meta.get("final_shape"),
        source_count=meta.get("source_count"),
    ).to_dict()

    # Semantic loss stage (analysis heuristic using benchmark labels only)
    loss_stage = "unknown"
    cats = set(c.get("failure_categories") or [])
    if "correct_op_grain_mismatch" in cats or obs.get("grain_match") is False:
        loss_stage = "grain_interpretation_or_collapse"
    if "correct_op_structural_mismatch" in cats:
        loss_stage = "projection_or_required_field_set"
    if diag.get("row_grain_with_collapsing_aggregate"):
        loss_stage = "collapse_after_join_or_union_under_row_grain_declaration"

    why_passed = [
        "plan_validation.valid implied by status=success",
        "execution.success implied by status=success",
        "result_validation.valid implied by status=success",
    ]
    if diag.get("row_grain_with_collapsing_aggregate"):
        why_passed.append(
            "Plan Validator treats row-level grain + aggregate as WARNING "
            "(final_grain_contradiction), not ERROR when required columns remain "
            "satisfiable after aggregate — so pipeline still succeeds."
        )
    grain = diag.get("declared_grain")
    if grain in {"group", "summary"} and diag.get("has_aggregate"):
        why_passed.append(
            "Declared collapsed grain + aggregate is internally consistent; "
            "Result Validator does not compare declared intent to user prompt."
        )

    return {
        "source": source,
        "run": run,
        "case_id_analysis_only": c.get("case_id"),
        "user_prompt_unavailable_in_frozen_json": True,
        "selected_operations": c.get("selected_operations"),
        "plan_requirements": plan.get("final_output_requirements"),
        "plan_steps": [
            {
                "op": s.get("op"),
                "params": {
                    k: (s.get("params") or {}).get(k)
                    for k in (
                        "left_keys",
                        "right_keys",
                        "group_by",
                        "metrics",
                        "columns",
                        "column_policy",
                    )
                    if (s.get("params") or {}).get(k) is not None
                },
            }
            for s in (plan.get("steps") or [])
        ],
        "expected_schema_by_step": meta.get("expected_schema_by_step"),
        "actual_schema_by_step": meta.get("actual_schema_by_step"),
        "final_shape": meta.get("final_shape"),
        "benchmark_expected_grain": obs.get("expected_grain"),
        "benchmark_actual_grain": obs.get("actual_grain"),
        "benchmark_evaluation_reason": obs.get("evaluation_reason"),
        "failure_categories": c.get("failure_categories"),
        "diagnostics": diag,
        "semantic_loss_stage": loss_stage,
        "why_existing_validators_passed": why_passed,
    }


def classify_family(trace: dict[str, Any]) -> str:
    diag = trace.get("diagnostics") or {}
    cats = set(trace.get("failure_categories") or [])
    if diag.get("row_grain_with_collapsing_aggregate"):
        return "declared_row_grain_vs_collapsing_aggregate"
    if "correct_op_grain_mismatch" in cats:
        return "declared_collapsed_grain_wrong_user_intent"
    if "correct_op_structural_mismatch" in cats:
        return "required_field_set_under_declaration"
    return "internally_consistent_wrong_intent"


def observability_bucket(family: str) -> str:
    if family == "declared_row_grain_vs_collapsing_aggregate":
        return "production_observable"
    if family == "required_field_set_under_declaration":
        return "potentially_observable"
    if family == "declared_collapsed_grain_wrong_user_intent":
        return "fundamentally_unobservable"
    return "fundamentally_unobservable"


def type_abcd(family: str) -> str:
    if family == "declared_row_grain_vs_collapsing_aggregate":
        return "D_validator_implementation_gap"  # warning exists but non-blocking
    if family == "required_field_set_under_declaration":
        return "B_contract_under_specification"
    return "C_fundamentally_runtime_invisible"


def confusion_for_flag(cases: list[dict[str, Any]], flag_fn) -> dict[str, Any]:
    TP = FP = TN = FN = 0
    fp_ids: list[str] = []
    tp_ids: list[str] = []
    for c in cases:
        if c.get("status") != "success":
            continue
        flagged = bool(flag_fn(c))
        silent = _is_silent(c)
        valid = _is_valid_success(c)
        cid = str(c.get("case_id"))
        if silent and flagged:
            TP += 1
            tp_ids.append(cid)
        elif silent and not flagged:
            FN += 1
        elif valid and flagged:
            FP += 1
            fp_ids.append(cid)
        elif valid and not flagged:
            TN += 1
    return {
        "TP": TP,
        "FP": FP,
        "TN": TN,
        "FN": FN,
        "precision": round(TP / (TP + FP), 3) if TP + FP else None,
        "recall": round(TP / (TP + FN), 3) if TP + FN else None,
        "false_positive_rate": round(FP / (FP + TN), 3) if FP + TN else None,
        "tp_case_ids": sorted(set(tp_ids)),
        "fp_case_ids": sorted(set(fp_ids)),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    traces: list[dict[str, Any]] = []
    all_success: list[dict[str, Any]] = []

    for label, root in (
        ("phase27_7b", PHASE27_7B),
        ("phase28_escalation", PHASE28_LIVE),
    ):
        for ri, run in enumerate(_load_runs(root), 1):
            for c in run.get("cases") or []:
                if c.get("status") == "success":
                    all_success.append(c)
                if _is_silent(c):
                    traces.append(build_trace(c, source=label, run=ri))

    # Taxonomy
    family_counts: Counter[str] = Counter()
    obs_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    for t in traces:
        fam = classify_family(t)
        t["failure_family"] = fam
        t["observability"] = observability_bucket(fam)
        t["phase29_type"] = type_abcd(fam)
        family_counts[fam] += 1
        obs_counts[t["observability"]] += 1
        type_counts[t["phase29_type"]] += 1

    # Candidate invariants
    def flag_row_grain_agg(c: dict[str, Any]) -> bool:
        d = observe_plan_diagnostics(c.get("plan") or {}, execution_meta=c.get("metadata") or {})
        return d.row_grain_with_collapsing_aggregate

    def flag_detail_agg(c: dict[str, Any]) -> bool:
        d = observe_plan_diagnostics(c.get("plan") or {})
        return d.declared_grain == "detail" and d.has_aggregate

    def flag_union_agg(c: dict[str, Any]) -> bool:
        ops = c.get("selected_operations") or []
        return "union_rows" in ops and "aggregate" in ops

    def flag_join_agg(c: dict[str, Any]) -> bool:
        ops = c.get("selected_operations") or []
        try:
            return ops.index("join") < ops.index("aggregate")
        except ValueError:
            return False

    candidates = {
        "row_grain_with_collapsing_aggregate": {
            "generic": True,
            "golden_independent": True,
            "domain_independent": True,
            "deterministic": True,
            "requires_new_planner_declaration": False,
            "notes": (
                "Uses declared final_output_requirements.grain ∈ {detail,entity} "
                "plus presence of aggregate in IntegrationPlan. Already emitted as "
                "Plan Validator WARNING (final_grain_contradiction); not blocking."
            ),
            "confusion": confusion_for_flag(all_success, flag_row_grain_agg),
            "production_candidate": "narrow_hardening_candidate",
        },
        "detail_grain_with_aggregate": {
            "generic": True,
            "golden_independent": True,
            "confusion": confusion_for_flag(all_success, flag_detail_agg),
            "production_candidate": "weak_on_current_residuals",
        },
        "union_then_aggregate": {
            "generic": True,
            "golden_independent": True,
            "confusion": confusion_for_flag(all_success, flag_union_agg),
            "production_candidate": "reject_high_fp",
        },
        "join_then_aggregate": {
            "generic": True,
            "golden_independent": True,
            "confusion": confusion_for_flag(all_success, flag_join_agg),
            "production_candidate": "reject_fp_on_join_aggregate",
        },
    }

    # Contract coverage audit (static narrative + counts)
    contract_audit = {
        "final_grain": {
            "declared_in_plan": True,
            "plan_validator_checks": "yes_soft_warning_on_row_grain_plus_aggregate",
            "result_validator_checks": "info_only_no_cardinality",
            "silent_cases_break": (
                "composite/three_file entity+aggregate: warning only; "
                "same_schema group+aggregate: contract satisfied, intent wrong"
            ),
        },
        "required_columns": {
            "declared_in_plan": True,
            "plan_validator_checks": "yes_error_if_unsatisfiable",
            "result_validator_checks": "yes_column_presence_only",
            "silent_cases_break": (
                "columns exist after aggregate; wrong semantic set vs user intent "
                "not detectable without external expected fields"
            ),
        },
        "one_row_represents": {
            "declared_in_plan": True,
            "plan_validator_checks": "info_only",
            "result_validator_checks": "none",
            "silent_cases_break": "unused for validation",
        },
        "join_keys": {
            "declared_in_plan": True,
            "plan_validator_checks": "structural_existence_and_safety",
            "result_validator_checks": "amplification_unmatched_partial",
            "silent_cases_break": "composite join keys were correct; residual was post-join aggregate",
        },
        "aggregation_semantics": {
            "declared_in_plan": "group_by_and_metrics",
            "plan_validator_checks": "structural",
            "result_validator_checks": "column_presence_uniqueness",
            "silent_cases_break": "necessity of aggregate vs user grain not checked",
        },
        "gaps": [
            "Contract exists but soft: row grain + aggregate → WARNING not ERROR",
            "Contract cannot express: user wanted detail rows but planner declared group",
            "Result Validator never re-checks grain vs collapse",
        ],
    }

    observability_matrix = {
        "silent_wrong_count": len(traces),
        "by_observability": dict(obs_counts),
        "by_family": dict(family_counts),
        "by_type": dict(type_counts),
        "observable_silent_wrong_count": obs_counts.get("production_observable", 0),
        "potentially_observable_count": obs_counts.get("potentially_observable", 0),
        "fundamentally_unobservable_count": obs_counts.get(
            "fundamentally_unobservable", 0
        ),
    }

    # Strong candidate summary
    strong = candidates["row_grain_with_collapsing_aggregate"]["confusion"]
    kpis = {
        "silent_wrong_count": len(traces),
        "observable_silent_wrong_count": observability_matrix[
            "observable_silent_wrong_count"
        ],
        "potentially_observable_count": observability_matrix[
            "potentially_observable_count"
        ],
        "fundamentally_unobservable_count": observability_matrix[
            "fundamentally_unobservable_count"
        ],
        "candidate_invariant_count": len(candidates),
        "strong_candidate": "row_grain_with_collapsing_aggregate",
        "candidate_TP": strong["TP"],
        "candidate_FP": strong["FP"],
        "candidate_FN": strong["FN"],
        "candidate_TN": strong["TN"],
        "false_positive_rate": strong["false_positive_rate"],
        "golden_independent_candidate_count": sum(
            1 for v in candidates.values() if v.get("golden_independent")
        ),
        "existing_contract_gap_count": 3,
        "validator_implementation_gap_count": 1,
        "planner_only_semantic_error_count": obs_counts.get(
            "fundamentally_unobservable", 0
        ),
        "phase28_baseline_unchanged": {
            "note": "Phase 29 adds diagnostics only; no validator/escalation changes",
            "expected_overall": 84.21,
            "expected_unsafe": 0.0,
            "expected_escalation_rate": 10.53,
        },
    }

    taxonomy = {
        "families": {
            "declared_row_grain_vs_collapsing_aggregate": {
                "definition": (
                    "Planner declares detail/entity grain but plan includes aggregate "
                    "that collapses rows; required columns remain satisfiable so "
                    "validators pass."
                ),
                "count": family_counts.get(
                    "declared_row_grain_vs_collapsing_aggregate", 0
                ),
                "type": "D",
                "observability": "production_observable",
            },
            "declared_collapsed_grain_wrong_user_intent": {
                "definition": (
                    "Planner declares group/summary + aggregate consistently, but "
                    "user intent (benchmark) was detail-preserving. Internally "
                    "consistent wrong interpretation."
                ),
                "count": family_counts.get(
                    "declared_collapsed_grain_wrong_user_intent", 0
                ),
                "type": "C",
                "observability": "fundamentally_unobservable",
            },
            "required_field_set_under_declaration": {
                "definition": (
                    "Declared required_columns omit fields needed for user-facing "
                    "output; plan/result consistent with declared set."
                ),
                "count": family_counts.get("required_field_set_under_declaration", 0),
                "type": "B",
                "observability": "potentially_observable",
            },
        }
    }

    (OUT / "silent_failure_traces.json").write_text(
        json.dumps({"traces": traces}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "failure_taxonomy.json").write_text(
        json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "contract_coverage_audit.json").write_text(
        json.dumps(contract_audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "candidate_invariants.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "invariant_counterexamples.json").write_text(
        json.dumps(
            {k: v.get("confusion") for k, v in candidates.items()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (OUT / "observability_matrix.json").write_text(
        json.dumps(observability_matrix, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "phase29_kpis.json").write_text(
        json.dumps(kpis, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(kpis, ensure_ascii=False, indent=2))
    print("families", dict(family_counts))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
