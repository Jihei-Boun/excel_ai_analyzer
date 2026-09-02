"""Phase 40E — SemanticRequirementContract v1 design (research only).

Does NOT modify production DSL, planner, Validator, Executor, verifier, or routing.
Design helpers may read existing V2.2 lineage; they are not wired into validation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from core.integrate.schema_lineage import build_schema_lineage
from core.integrate.semantic_escalation import (
    MAX_SEMANTIC_ESCALATIONS,
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
)
from core.integrate.result_observation import (
    MAX_RESULT_SAMPLE_COLUMNS,
    MAX_RESULT_SAMPLE_ROWS,
    MAX_RESULT_SERIALIZED_CHARS,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase40e"
PHASE40D_SHA = "b1382d0fad1656aa0e5328885cede2a73b060620"

CONTRACT_VERSION = "1"
GROUNDING = frozenset({"grounded", "cannot_ground"})
CHECK_OUTCOMES = frozenset({
    "SATISFIED",
    "CONTRADICTION",
    "INDETERMINATE",
    "NOT_APPLICABLE",
    "INVALID_CONTRACT",
    "OPERATIONAL_FAILURE",
})

# Option B: grain + explicit binding. No function/output/distinction/relationship.
V1_SCHEMA = {
    "contract_version": CONTRACT_VERSION,
    "grounding_status": "grounded | cannot_ground",
    "required_grain": [
        {
            "role_id": "g1",
            "semantic_label": "diagnostic only; Python must not branch on this",
            "binding": {"source_id": "src_a", "column_ref": "key_col"},
            "grounding_status": "grounded | cannot_ground",
            "required_for_answerability": True,
        }
    ],
}

REMOVED_FROM_40D = [
    "partially_grounded",
    "cannot_determine",
    "required_outputs",
    "function",
    "required_distinctions",
    "required_relations",
]


def _write(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def valid_example_grounded() -> dict[str, Any]:
    return {
        "contract_version": "1",
        "grounding_status": "grounded",
        "required_grain": [
            {
                "role_id": "g1",
                "semantic_label": "requested grouping entity",
                "binding": {"source_id": "src_a", "column_ref": "entity_key"},
                "grounding_status": "grounded",
                "required_for_answerability": True,
            }
        ],
    }


def valid_example_cannot_ground() -> dict[str, Any]:
    return {
        "contract_version": "1",
        "grounding_status": "cannot_ground",
        "required_grain": [
            {
                "role_id": "g1",
                "semantic_label": "requested distinction absent from schema",
                "binding": None,
                "grounding_status": "cannot_ground",
                "required_for_answerability": True,
            }
        ],
    }


def invalid_examples() -> list[dict[str, Any]]:
    return [
        {"id": "missing_version", "why": "contract_version required", "obj": {"grounding_status": "grounded", "required_grain": []}},
        {"id": "partially_grounded_forbidden", "why": "v1 removed ambiguous partial state", "obj": {**valid_example_grounded(), "grounding_status": "partially_grounded"}},
        {"id": "binding_without_source", "why": "binding must be source_id+column_ref or null", "obj": {
            **valid_example_grounded(),
            "required_grain": [{
                "role_id": "g1", "semantic_label": "x",
                "binding": {"column_ref": "entity_key"},
                "grounding_status": "grounded", "required_for_answerability": True,
            }],
        }},
        {"id": "fabricated_binding_on_cannot_ground", "why": "cannot_ground role must have binding null", "obj": {
            **valid_example_cannot_ground(),
            "required_grain": [{
                "role_id": "g1", "semantic_label": "x",
                "binding": {"source_id": "src_a", "column_ref": "invented"},
                "grounding_status": "cannot_ground", "required_for_answerability": True,
            }],
        }},
        {"id": "python_must_not_fill_empty_grain", "why": "missing required_grain is missing, not inferred", "obj": {
            "contract_version": "1", "grounding_status": "grounded",
        }},
    ]


def parse_contract_structural(raw: Any, schemas: dict[str, list[str]]) -> dict[str, Any]:
    """Structural parse only. No label matching, no autocomplete."""
    if not isinstance(raw, dict):
        return {"valid": False, "reason": "not_object", "outcome": "INVALID_CONTRACT"}
    if str(raw.get("contract_version")) != CONTRACT_VERSION:
        return {"valid": False, "reason": "bad_version", "outcome": "INVALID_CONTRACT"}
    gs = raw.get("grounding_status")
    if gs not in GROUNDING:
        return {"valid": False, "reason": "bad_grounding_status", "outcome": "INVALID_CONTRACT"}
    grains = raw.get("required_grain")
    if not isinstance(grains, list):
        return {"valid": False, "reason": "required_grain_not_list", "outcome": "INVALID_CONTRACT"}
    allowed = {(sid, col) for sid, cols in schemas.items() for col in cols}
    roles = []
    for it in grains:
        if not isinstance(it, dict) or not it.get("role_id"):
            return {"valid": False, "reason": "role_missing_id", "outcome": "INVALID_CONTRACT"}
        rgs = it.get("grounding_status")
        if rgs not in GROUNDING:
            return {"valid": False, "reason": "role_bad_grounding", "outcome": "INVALID_CONTRACT"}
        bind = it.get("binding")
        if rgs == "cannot_ground":
            if bind is not None:
                return {"valid": False, "reason": "cannot_ground_must_not_bind", "outcome": "INVALID_CONTRACT"}
            roles.append({
                "role_id": str(it["role_id"]),
                "binding": None,
                "grounding_status": rgs,
                "required_for_answerability": bool(it.get("required_for_answerability")),
            })
            continue
        if not isinstance(bind, dict):
            return {"valid": False, "reason": "grounded_role_needs_binding", "outcome": "INVALID_CONTRACT"}
        sid, col = str(bind.get("source_id") or ""), str(bind.get("column_ref") or "")
        if not sid or not col:
            return {"valid": False, "reason": "binding_incomplete", "outcome": "INVALID_CONTRACT"}
        if (sid, col) not in allowed:
            return {"valid": False, "reason": "binding_not_in_schema", "outcome": "INVALID_CONTRACT"}
        roles.append({
            "role_id": str(it["role_id"]),
            "binding": {"source_id": sid, "column_ref": col},
            "grounding_status": rgs,
            "required_for_answerability": bool(it.get("required_for_answerability")),
        })
    if gs == "grounded" and not any(r["grounding_status"] == "grounded" for r in roles):
        return {"valid": False, "reason": "grounded_without_bound_role", "outcome": "INVALID_CONTRACT"}
    if gs == "cannot_ground" and not any(r["grounding_status"] == "cannot_ground" for r in roles):
        return {"valid": False, "reason": "cannot_ground_without_unbound_role", "outcome": "INVALID_CONTRACT"}
    return {
        "valid": True,
        "outcome": None,
        "grounding_status": gs,
        "required_grain": roles,
        # semantic_label intentionally dropped for checker use
    }


def _origin_hits(lineage: dict[str, Any], source_id: str, column_ref: str) -> list[str]:
    hits = []
    origins = lineage.get("final_column_origins") or {}
    for final_col, rows in origins.items():
        for o in rows or []:
            if isinstance(o, dict) and o.get("source") == source_id and o.get("column") == column_ref:
                hits.append(str(final_col))
                break
    return hits


def observe_required_ungrounded(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Observe LLM-declared necessity + cannot_ground as a fact only.

    Does NOT choose cannot_plan, retry, escalation, or any planner outcome.
    Pipeline policy for this fact is OUT_OF_SCOPE_FOR_IMPLEMENTATION.
    """
    facts = []
    if not parsed.get("valid"):
        return facts
    for role in parsed.get("required_grain") or []:
        if role.get("required_for_answerability") and role.get("grounding_status") == "cannot_ground":
            facts.append({
                "role_id": role["role_id"],
                "fact": "REQUIRED_OBLIGATION_UNGROUNDED",
                "pipeline_action": None,
            })
    return facts


def check_plan_contract(
    parsed: dict[str, Any],
    *,
    plan: dict[str, Any] | None,
    schemas: dict[str, list[str]],
    generation_error: str | None = None,
) -> dict[str, Any]:
    """Design-only checker. Uses V2.2 origins, never semantic_label or user prompt."""
    if generation_error:
        return {"status": "OPERATIONAL_FAILURE", "detail": generation_error}
    if not parsed.get("valid"):
        return {"status": "INVALID_CONTRACT", "detail": parsed.get("reason")}
    answerability_facts = observe_required_ungrounded(parsed)
    status = (plan or {}).get("status")
    if status == "cannot_plan":
        return {
            "status": "NOT_APPLICABLE",
            "detail": "cannot_plan has no materialized grain; do not treat empty schema as contradiction",
            "answerability_facts": answerability_facts,
            "findings": [
                {"rule": "DECLARED_BINDING_EXISTS", "status": "NOT_APPLICABLE"},
                {"rule": "DECLARED_GRAIN_PRESERVED", "status": "NOT_APPLICABLE"},
            ],
        }
    if status != "planned" or not plan:
        return {"status": "INDETERMINATE", "detail": "no planned graph"}
    lineage = build_schema_lineage(plan, schemas)
    findings = []
    for role in parsed["required_grain"]:
        if role["grounding_status"] == "cannot_ground":
            findings.append({"role_id": role["role_id"], "rule": "DECLARED_BINDING_EXISTS", "status": "NOT_APPLICABLE"})
            if role.get("required_for_answerability"):
                findings.append({
                    "role_id": role["role_id"],
                    "rule": "REQUIRED_OBLIGATION_UNGROUNDED",
                    "status": "FACT",
                    "pipeline_action": None,
                })
            continue
        b = role["binding"]
        hits = _origin_hits(lineage, b["source_id"], b["column_ref"])
        if not lineage.get("final_column_origins"):
            findings.append({"role_id": role["role_id"], "rule": "DECLARED_GRAIN_PRESERVED", "status": "INDETERMINATE"})
            continue
        group_by = []
        for step in plan.get("steps") or []:
            if isinstance(step, dict) and step.get("op") == "aggregate":
                group_by = [str(x) for x in ((step.get("params") or {}).get("group_by") or [])]
        if group_by:
            # Contradiction only if no surviving origin maps to a group_by display name
            # AND lineage proves the origin is absent from group identities.
            surviving_in_gb = [c for c in hits if c in group_by]
            origin_in_any_final = bool(hits)
            if surviving_in_gb:
                findings.append({"role_id": role["role_id"], "rule": "DECLARED_GRAIN_PRESERVED", "status": "SATISFIED", "via": surviving_in_gb})
            elif origin_in_any_final:
                # Present in final but not as grain — may be a measure column; grain collapse is proven
                # only when aggregate ran and origin is not among group_by identities.
                findings.append({"role_id": role["role_id"], "rule": "DECLARED_GRAIN_PRESERVED", "status": "CONTRADICTION", "via": hits})
            else:
                # Origin not in final at all after aggregate → proven collapsed/absent
                findings.append({"role_id": role["role_id"], "rule": "DECLARED_GRAIN_PRESERVED", "status": "CONTRADICTION", "via": []})
        else:
            if hits:
                findings.append({"role_id": role["role_id"], "rule": "DECLARED_GRAIN_PRESERVED", "status": "SATISFIED", "via": hits})
            elif lineage.get("final_schema") is None:
                findings.append({"role_id": role["role_id"], "rule": "DECLARED_GRAIN_PRESERVED", "status": "INDETERMINATE"})
            else:
                findings.append({"role_id": role["role_id"], "rule": "DECLARED_GRAIN_PRESERVED", "status": "CONTRADICTION", "via": []})
    statuses = [f["status"] for f in findings if f["status"] != "FACT"]
    if "CONTRADICTION" in statuses:
        overall = "CONTRADICTION"
    elif statuses and all(s == "INDETERMINATE" for s in statuses):
        overall = "INDETERMINATE"
    elif statuses and all(s in {"SATISFIED", "NOT_APPLICABLE"} for s in statuses):
        overall = "SATISFIED" if any(s == "SATISFIED" for s in statuses) else "NOT_APPLICABLE"
    elif "INDETERMINATE" in statuses:
        overall = "INDETERMINATE"
    else:
        overall = "SATISFIED"
    return {
        "status": overall,
        "findings": findings,
        "answerability_facts": answerability_facts,
        "does_not_set_cannot_plan": True,
    }


def rename_demo_schemas() -> dict[str, list[str]]:
    return {"src_a": ["entity_key", "measure"]}


def rename_then_select_plan() -> dict[str, Any]:
    return {
        "status": "planned",
        "final_output": "s",
        "steps": [
            {
                "id": "r1", "op": "rename_columns", "inputs": ["src_a"], "output": "r",
                "params": {"mapping": {"measure": "brightness", "entity_key": "id_out"}},
            },
            {
                "id": "s1", "op": "select_columns", "inputs": ["r"], "output": "s",
                "params": {"columns": ["id_out", "brightness"]},
            },
        ],
    }


def wrong_group_plan() -> dict[str, Any]:
    return {
        "status": "planned",
        "final_output": "a",
        "steps": [{
            "id": "a1", "op": "aggregate", "inputs": ["src_a"], "output": "a",
            "params": {
                "group_by": ["measure"],
                "metrics": [{"column": "measure", "function": "sum", "alias": "measure"}],
            },
        }],
    }


def write_artifacts() -> None:
    _write("baseline_freeze.json", {
        "phase40d_sha": PHASE40D_SHA,
        "phase40d_gate": "A",
        "shadow": "OFF",
        "migration": "NOT_APPROVED",
        "production_verifier": SEMANTIC_VERIFIER_MODEL,
        "production_variant": SEMANTIC_VERIFIER_VARIANT,
        "bounded_result_v1": {
            "MAX_RESULT_SAMPLE_ROWS": MAX_RESULT_SAMPLE_ROWS,
            "MAX_RESULT_SAMPLE_COLUMNS": MAX_RESULT_SAMPLE_COLUMNS,
            "MAX_RESULT_SERIALIZED_CHARS": MAX_RESULT_SERIALIZED_CHARS,
        },
        "MAX_SEMANTIC_ESCALATIONS": MAX_SEMANTIC_ESCALATIONS,
        "production_changed": False,
    })
    _write("phase40d_metric_corrections.json", {
        "FALSE_BLOCK": {
            "raw_checker_contradiction_is_not_semantic_failure": True,
            "32B_I0_semantic": 0.0,
            "32B_I0_observation_gap": 0.1538,
            "7B_I0_semantic": 0.0385,
            "7B_I0_observation_gap": 0.0769,
            "observation_gap_kinds": ["rename_display_name", "cannot_plan_empty_schema"],
        },
        "SELF_JUSTIFICATION": {
            "definition": "Manual NO + usable wrong contract + internally consistent with wrong plan",
            "7B_I0": 0.0588,
            "32B_I0": 0.0,
            "unusable_excluded": ["timeout", "empty_contract", "parser_failure"],
        },
    })
    _write("contract_v1_candidate.json", {
        "name": "SemanticRequirementContract",
        "version": CONTRACT_VERSION,
        "option": "B",
        "schema": V1_SCHEMA,
        "removed": REMOVED_FROM_40D,
        "valid_grounded": valid_example_grounded(),
        "valid_cannot_ground": valid_example_cannot_ground(),
    })
    _write("contract_field_decision_table.json", [
        {"field": "contract_version", "semantic_value": "none", "checkable": True, "evidence_40d": "versioning", "complexity": "low", "include_v1": True},
        {"field": "grounding_status", "semantic_value": "LLM-authored bindability", "checkable": "structural enum only", "evidence_40d": "needed for abstention", "complexity": "low", "include_v1": True},
        {"field": "partially_grounded", "semantic_value": "ambiguous", "checkable": False, "evidence_40d": "no deterministic checker behavior", "complexity": "medium", "include_v1": False},
        {"field": "required_grain.role_id", "semantic_value": "stable id", "checkable": True, "evidence_40d": "identity handle", "complexity": "low", "include_v1": True},
        {"field": "required_grain.semantic_label", "semantic_value": "LLM meaning (diagnostic)", "checkable": False, "evidence_40d": "Python must not read", "complexity": "low", "include_v1": "diagnostic_only"},
        {"field": "required_grain.binding", "semantic_value": "LLM maps role→schema", "checkable": "existence + lineage", "evidence_40d": "core value", "complexity": "low", "include_v1": True},
        {"field": "required_grain.required_for_answerability", "semantic_value": "LLM necessity declaration", "checkable": "observable fact REQUIRED_OBLIGATION_UNGROUNDED only", "evidence_40d": "C-like abstention", "complexity": "low", "include_v1": True, "python_must_not": ["cannot_plan", "semantic retry", "strong-model escalation", "planner outcome"]},
        {"field": "required_outputs / function", "semantic_value": "metric role", "checkable": "only tiny enum", "evidence_40d": "recall up, false-block/complexity up", "complexity": "medium", "include_v1": False},
        {"field": "required_distinctions", "semantic_value": "pair distinctness", "checkable": "signature identity only", "evidence_40d": "small incremental value", "complexity": "medium", "include_v1": False},
        {"field": "relationship", "semantic_value": "join/compare ontology", "checkable": False, "evidence_40d": "hidden planner risk", "complexity": "high", "include_v1": False},
    ])
    _write("contract_invariants.json", [
        "SEMANTIC_REQUIREMENT_INDEPENDENCE: declaration must not inspect IntegrationPlan",
        "LLM authors meaning, binding, necessity",
        "Python never branches on semantic_label or user prompt",
        "Missing field stays missing; no group_by autocomplete",
        "cannot_ground does not imply cannot_plan",
        "required_for_answerability=true + cannot_ground → FACT REQUIRED_OBLIGATION_UNGROUNDED only",
        "Python must not map that fact to cannot_plan, retry, escalation, or planner outcome",
        "LLM declares necessity; pipeline policy for unsatisfied necessity is out of scope",
        "Insufficient lineage → INDETERMINATE, not CONTRADICTION",
        "cannot_plan → grain preservation NOT_APPLICABLE",
        "Operational failure stays operational",
        "Immutable within the same semantic-evidence snapshot across planner attempts/retries",
        "Planner failure does not rewrite the contract",
        "New semantic_contract_version only if CrossFileUnderstanding/evidence is explicitly re-resolved; new immutable artifact, not mutation",
        "Re-resolution is not implemented in Phase 40E",
        "Verifier remains required",
    ])
    _write("contract_invalid_examples.json", invalid_examples())
    _write("binding_model.json", {
        "shape": {"source_id": "CrossFileUnderstanding source_id", "column_ref": "observed column name at that source"},
        "authority": "LLM",
        "python": ["tuple exists in schema inventory", "origin survives in V2.2 final_column_origins"],
        "wrong_binding": "SEMANTIC_BINDING_ERROR; Python does not repair",
        "no_parallel_expression_language": True,
    })
    _write("grounding_state_model.json", {
        "v1_states": sorted(GROUNDING),
        "removed": ["partially_grounded"],
        "cannot_ground": "semantically understood, no safe schema binding; binding must be null",
        "cannot_ground_vs_cannot_plan": "Python observes REQUIRED_OBLIGATION_UNGROUNDED; does not choose cannot_plan",
        "required_for_answerability": {
            "author": "LLM",
            "python_may_observe": "required_for_answerability=true AND grounding_status=cannot_ground",
            "python_max_conclusion": "REQUIRED_OBLIGATION_UNGROUNDED",
            "python_must_not_decide": ["cannot_plan", "semantic retry", "strong-model escalation", "planner outcome"],
            "policy_owner": "separate semantic/pipeline policy design; OUT_OF_SCOPE_FOR_IMPLEMENTATION",
        },
    })
    _write("contract_status_algebra.json", {
        "outcomes": sorted(CHECK_OUTCOMES),
        "meanings": {
            "SATISFIED": "declared bindings structurally preserved",
            "CONTRADICTION": "lineage proves declared binding not preserved as required grain",
            "INDETERMINATE": "observation insufficient; do not block",
            "NOT_APPLICABLE": "cannot_plan or ungrounded role; no grain check",
            "INVALID_CONTRACT": "structural parse/reference failure",
            "OPERATIONAL_FAILURE": "timeout/backend/parser crash",
        },
        "not_boolean": True,
    })
    _write("contract_plan_checker_spec.json", {
        "allowed": ["DECLARED_BINDING_EXISTS", "DECLARED_GRAIN_PRESERVED", "REQUIRED_OBLIGATION_UNGROUNDED (fact only)"],
        "uses": ["parsed bindings", "V2.2 final_column_origins", "plan.status", "aggregate group_by display names only as lineage keys"],
        "never": ["user_prompt", "semantic_label", "benchmark family"],
        "false_block_rule": "block only on proven CONTRADICTION",
    })
    _write("checker_allowed_inputs.json", {
        "inputs": ["structurally parsed contract", "source schema inventory", "IntegrationPlan dict", "build_schema_lineage output"],
    })
    _write("checker_forbidden_inference.json", {
        "forbidden": [
            "interpret user prompt",
            "decide correct business grain",
            "map words to columns",
            "fuzzy match roles",
            "choose join vs union",
            "decide aggregation semantics",
            "metric equivalence",
            "fix bindings",
            "fill omitted required_grain from group_by",
            "map required_for_answerability to cannot_plan",
            "map required_for_answerability to retry or escalation",
        ]
    })
    _write("rename_lineage_design.json", {
        "v2_2_already": "rename_columns copies source_origins and evidence signatures to the new display name",
        "40d_gap": "research checker compared literal column_ref to final_schema display names",
        "v1_rule": "resolve binding via final_column_origins (source, column), not display name",
        "invariant": "rename changes display name; ancestry is unchanged",
        "if_origins_missing": "INDETERMINATE",
        "implement_in_40e": False,
    })
    _write("cannot_plan_contract_design.json", {
        "paths": ["planned", "cannot_plan", "planner_failure"],
        "cannot_plan": "NOT_APPLICABLE for grain preservation; empty final_schema is not contradiction",
        "cannot_ground_not_auto_cannot_plan": True,
        "required_for_answerability_max_python_conclusion": "REQUIRED_OBLIGATION_UNGROUNDED",
        "pipeline_policy": "OUT_OF_SCOPE_FOR_IMPLEMENTATION",
    })
    _write("observation_gap_resolution_spec.json", {
        "rename": "use V2.2 origins (already present)",
        "aggregate": "group_by identities compared through origin→current display map",
        "cannot_plan": "skip grain checks",
        "insufficient_proof": "INDETERMINATE",
        "precondition_for_implementation": "research checker must be re-run on 40D corpus with origin identity before any production wiring",
    })
    _write("independence_invariant.json", {
        "name": "SEMANTIC_REQUIREMENT_INDEPENDENCE",
        "declaration_must_not_see": ["IntegrationPlan", "validator result", "executor result", "verifier verdict"],
        "reason_40d": "7B I1 wrong-case exposure 0.53 → 0.18",
        "do_not_merge_calls_yet": True,
    })
    _write("request_attempt_scope.json", {
        "scope": "request",
        "immutable_within_same_semantic_evidence_snapshot": True,
        "planner_attempts_and_retries": "contract remains immutable",
        "planner_failure_does_not_rewrite": True,
        "attachment": "identifier-bound to request, never completion-order",
        "new_version_only_if": "CrossFileUnderstanding or semantic evidence is explicitly re-resolved by a future architecture",
        "new_version_is": "a new immutable artifact with lineage to the prior contract; not an in-place mutation",
        "re_resolution_implemented_in_40e": False,
        "ids": ["semantic_contract_id", "semantic_contract_version", "contract_generation_invocation_id", "contract_parent_id"],
        "concurrency": ["request-local", "immutable once attached", "identifier-bound", "never completion-order", "not global"],
    })
    _write("failure_algebra.json", {
        "distinct": [
            "contract generation backend failure → OPERATIONAL_FAILURE",
            "parser failure → INVALID_CONTRACT",
            "cannot_ground → contract state, not cannot_plan",
            "required_for_answerability+cannot_ground → REQUIRED_OBLIGATION_UNGROUNDED fact only",
            "contract-plan contradiction → CONTRADICTION",
            "observation indeterminate → INDETERMINATE",
        ],
        "precedence": ["safety/structural invalidity", "OPERATIONAL_FAILURE", "INVALID_CONTRACT", "CONTRADICTION", "INDETERMINATE"],
        "retry": "OUT_OF_SCOPE_FOR_IMPLEMENTATION",
    })
    _write("pipeline_order_options.json", {
        "option_A_checker_then_validator": "wastes less validator work on contradictions but lineage may be incomplete",
        "option_B_validator_then_checker": "preferred: lineage requires a structurally valid planned graph",
        "cannot_plan": "validator/planner status first; checker returns NOT_APPLICABLE",
        "recommendation": "B",
        "implemented": False,
    })
    _write("d1_vs_d2_analysis.json", {
        "D1": "independent contract hidden from planner — strongest independence; more disagreement",
        "D2": "independent contract supplied to planner — shared objective; wrong contract propagates",
        "40d": "I0 (declaration without plan) beat I1 (declaration with plan). D2 is not I1, but still couples planner to a possibly wrong declaration",
        "authority": "contract = requirement declaration; plan = execution proposal; Python does not pick a semantic winner",
        "v1_recommendation": "D1 for first design; D2 operational only after declaration quality evidence",
        "implemented": False,
    })
    _write("contract_verifier_complementarity.json", {
        "contract_checker": "declared obligation vs plan/lineage",
        "verifier": "declaration/plan/result appropriate for the user",
        "residual_impossible_for_checker": ["wrong binding", "omitted obligation", "uncheckable semantics", "campus-like misbind"],
        "verifier_still_required": True,
    })
    _write("architecture_leakage_audit.json", [
        {"element": "semantic_label", "llm_authored": True, "python_only_declared_bindings": True, "ok": True, "note": "diagnostic; dropped before checker"},
        {"element": "binding.source_id/column_ref", "llm_authored": True, "python_only_declared_bindings": True, "ok": True},
        {"element": "required_for_answerability", "llm_authored": True, "python_only_declared_bindings": True, "ok": True, "note": "FACT REQUIRED_OBLIGATION_UNGROUNDED only; no pipeline policy"},
        {"element": "DECLARED_GRAIN_PRESERVED", "llm_authored": False, "python_only_declared_bindings": True, "ok": True},
        {"element": "function enum", "rejected": True, "reason": "40D false-block/complexity without observation-safe identity"},
    ])
    _write("complexity_budget.json", {
        "target": "required grain → grounded expression → deterministic preservation",
        "v1": "grounding_status + required_grain(role_id, binding, necessity flag)",
        "rejected_for_size": REMOVED_FROM_40D,
        "additional_llm_call": True,
        "call_not_approved": True,
    })
    _write("future_implementation_preconditions.json", {
        "must_prove_first": [
            "origin-based checker closes 40D rename/cannot_plan observation gaps on frozen corpus",
            "Manual YES semantic blocker rate after gaps removed",
            "I0 independence preserved if any call consolidation is proposed",
            "no 32B default; model strategy separate",
            "no production DSL/planner/validator wiring until those hold",
        ],
        "next": "Outcome A then B: prove observation, then operational cost; do not implement contract",
        "migration": "NOT_APPROVED",
    })
    _write("shadow_state_proof.json", {
        "shadow": "OFF",
        "env_MULTI_SHADOW_ENABLED": os.environ.get("MULTI_SHADOW_ENABLED", ""),
    })
    _write("phase40e_summary.json", {
        "gate": "A",
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
        "production_change": "NO_PRODUCTION_CHANGE",
        "verdict_option": "B",
        "contract": "SemanticRequirementContract v1",
        "independence": "SEMANTIC_REQUIREMENT_INDEPENDENCE / D1",
        "checker_order": "structural Validator then contract checker",
        "partially_grounded": "REMOVED",
        "function_outputs_distinctions": "DEFER/REMOVE",
        "next": "A_then_B",
        "phase40d_sha": PHASE40D_SHA,
        "required_for_answerability_max": "REQUIRED_OBLIGATION_UNGROUNDED",
        "answerability_pipeline_policy": "OUT_OF_SCOPE_FOR_IMPLEMENTATION",
        "immutability": "same_semantic_evidence_snapshot",
        "reresolution": "future new immutable version only; not implemented",
    })


def main() -> None:
    write_artifacts()
    print("wrote", OUT)


if __name__ == "__main__":
    main()
