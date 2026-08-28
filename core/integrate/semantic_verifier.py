"""Phase 33: offline semantic wrong-success verification (research only).

Judges whether a successful IntegrationPlan/result satisfies the user request.
Does NOT mutate plans, execute repairs, wire into production pipeline, or
use golden/scenario labels as verifier input.\n\nPhase 39B: independent reconstruction from raw user_prompt; planner\nlabels/roles are claims, not ground truth.\n"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from core.llm_client import chat_json

VERDICTS = frozenset({"pass", "fail", "uncertain"})

REASON_CODES = frozenset(
    {
        "missing_requested_output",
        "wrong_output_grain",
        "unsupported_aggregation",
        "incomplete_source_integration",
        "plan_request_mismatch",
        "result_plan_mismatch",
        "insufficient_evidence",
        "satisfied",
        "other",
    }
)

_VERIFIER_SYSTEM = """
You are a Semantic Verifier for a multi-file Excel integration system.
You JUDGE whether a proposed IntegrationPlan (and optional observed result)
directly satisfies the user's request.

Independence protocol (CRITICAL — Phase 39B):
1) From raw user_prompt ALONE, reconstruct what material information must
   remain observable to answer the request. Do this BEFORE trusting claims.
2) Inspect plan_structure ops/params and decide whether that information
   survives the operation sequence.
3) Only afterward inspect planner_claims (final_output_requirements,
   output_roles, one_row_represents, reason, notes). These are CLAIMS from
   the same planner — never ground truth. Agreement with claims alone is
   NEVER sufficient for pass.
4) Generic operation effects (not domain rules):
   - union_rows: stacks rows; does NOT automatically preserve source identity
   - aggregate: keeps group_by + produced metrics; collapses other distinctions
   - select_columns: drops omitted columns
   - rename_columns: preserves information under new names
   - join: can retain side-specific columns from both inputs
   - filter_rows: keeps schema; may relate already-present columns
5) union_rows then aggregate is NOT inherently wrong. It is wrong only when
   it destroys information materially required for THIS request (e.g. collapsing
   two required contrast sides into one total). Combined totals / appends that
   intentionally collapse sources may be correct when distinction is not needed.
5b) Requests that only ask to combine/stack/append compatible tables into one
   table, or to compute an overall/by-key total across inputs, do NOT require
   preserving source identity. Do NOT invent a contrast requirement that the
   user did not ask for.
6) If the request needs values from each side of a material distinction to be
   observable (contrast, difference, which side changed), and plan_structure
   collapses those sides into a single metric before that observation is
   possible, you MUST fail — even when planner_claims label the result as a
   "comparison" or list only the collapsed columns.
7) Never justify pass by quoting planner_claims.one_row_represents, notes,
   reason, or output_roles alone. Those fields cannot rescue a collapsing plan.
8) Materialization grounding (Phase 39D — CRITICAL when materialization_evidence
   is present):
   - Treat materialization_evidence as deterministic structural fact about which
     columns exist after each op (and which referenced columns do NOT exist).
   - Claims / narrative / role labels that name columns absent from
     final_schema, or listed in unresolved_column_refs /
     claimed_columns_absent_from_final, are NOT proof those values exist.
   - Join producing side-specific surviving columns (e.g. metric_left and
     metric_right) PRESERVES distinction; it is NOT aggregation collapse.
   - Rename then join that yields distinct final metric columns (e.g. metric_a
     and metric_b) also PRESERVES distinction when those columns appear in
     final_schema — _left/_right suffixes are NOT required.
   - Entity grain (one row per key) with multiple distinct side metric columns
     is NOT wrong_output_grain / collapse. Collapse means a required side is
     absent from final_schema or merged into a single combined metric.
   - When final_schema contains two side metric columns with DIFFERENT
     evidence_signatures (especially different row_population.filters), those
     are independently partitioned sides even if they share a source file.
     One row per entity_id that still carries both side columns PRESERVES
     distinction — do not call that collapse / wrong_output_grain.
   - When materialization_evidence.final_column_origins shows two final metric
     columns deriving from different source inputs, treat them as independently
     surviving columns — not one collapsed total — UNLESS evidence signatures
     show they are identical expressions over the same row population.
   - Shared source origin alone does NOT prove fake dual sides. Independent
     partition/filter ancestry (different row_population.filters) can create
     genuine comparison sides from the same source file/column.
   - CRITICAL (Phase 39H provenance independence): If the user request needs
     independently grounded comparison sides, and
     identical_evidence_signature_column_sets / equivalent_evidence_signature_groups
     places those claimed side metrics in the SAME set (identical aggregate
     function + input column + group_by + row_population/filters), you MUST
     fail. Two aliases of the same expression over the same row population are
     NOT two sides. Distinct column names, suffixes, or output_roles do NOT
     create independence.
   - Conversely, different evidence_signatures (especially different filters
     or different source lineages) may be independent even when source files
     overlap. Do NOT fail merely because origins share a source file.
   - Deterministic materialization_evidence outranks planner narrative about
     "collapse" when final_schema objectively retains the required sides.
   - Do NOT invent an aggregation step that is not in plan_structure.
   - Conceptual levels (reasoning aid only, not taxonomy routing):
     L1 distinction preserved (both sides observable) vs L3 requested relation
     (increase/filter/rank) — if the request only needs side-by-side contrast,
     L1/L2 can suffice; do not demand an explicit delta unless the request
     clearly needs that relation.

Rules:
- Judge only. Do NOT rewrite the plan, choose keys, change aggregations,
  repair results, invent columns, or invent operations.
- Use only the evidence provided in the user message.
- If evidence is insufficient to decide, return verdict=uncertain.
- Do not assume domain knowledge beyond the provided observations.
- Prefer fail only when there is a clear semantic mismatch with the request.
- Prefer pass only when the plan/result clearly answers the request.
- Prefer uncertain when you cannot tell from the evidence.

Return ONE JSON object only:
{
  "verdict": "pass" | "fail" | "uncertain",
  "reason_code": "<one of: satisfied, missing_requested_output, wrong_output_grain, unsupported_aggregation, incomplete_source_integration, plan_request_mismatch, result_plan_mismatch, insufficient_evidence, other>",
  "evidence": ["short evidence bullets"]
}
""".strip()


@dataclass
class SemanticVerificationResult:
    verdict: str  # pass | fail | uncertain | parse_failed
    reason_code: str | None = None
    evidence: list[str] = field(default_factory=list)
    raw: dict[str, Any] | None = None
    parse_ok: bool = True
    model: str | None = None
    variant: str | None = None
    elapsed_s: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compact_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None
    steps = []
    for s in plan.get("steps") or []:
        if not isinstance(s, dict):
            continue
        steps.append(
            {
                "id": s.get("id"),
                "op": s.get("op"),
                "inputs": list(s.get("inputs") or []),
                "output": s.get("output"),
                "params": s.get("params") or {},
            }
        )
    return {
        "status": plan.get("status"),
        "steps": steps,
        "final_output": plan.get("final_output"),
        "final_output_requirements": plan.get("final_output_requirements"),
        "reason": plan.get("reason"),
        "notes": list(plan.get("notes") or [])[:4],
    }




def _plan_structure_only(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """Structural plan evidence without planner narrative fields."""
    if not isinstance(plan, dict):
        return None
    steps = []
    for s in plan.get("steps") or []:
        if not isinstance(s, dict):
            continue
        steps.append(
            {
                "id": s.get("id"),
                "op": s.get("op"),
                "inputs": list(s.get("inputs") or []),
                "output": s.get("output"),
                "params": s.get("params") or {},
            }
        )
    return {
        "status": plan.get("status"),
        "steps": steps,
        "final_output": plan.get("final_output"),
    }


def _planner_claims(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """Planner self-declarations — claims only, never ground truth."""
    if not isinstance(plan, dict):
        return None
    claims: dict[str, Any] = {}
    req = plan.get("final_output_requirements")
    if isinstance(req, dict) and req:
        claims["final_output_requirements"] = req
    if plan.get("reason") not in (None, ""):
        claims["reason"] = plan.get("reason")
    notes = list(plan.get("notes") or [])[:4]
    if notes:
        claims["notes"] = notes
    return claims or None

def _compact_understanding(und: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(und, dict):
        return None
    profiles = []
    for p in und.get("file_profiles") or []:
        if not isinstance(p, dict):
            continue
        obs = p.get("observations") or {}
        cols = []
        for c in obs.get("columns") or []:
            if not isinstance(c, dict):
                continue
            cols.append(
                {
                    "name": c.get("name"),
                    "dtype_family": c.get("dtype_family"),
                    "sample_values": list(c.get("sample_values") or [])[:3],
                }
            )
        profiles.append({"source_id": p.get("source_id"), "columns": cols})
    rels = []
    for r in und.get("relationships") or []:
        if not isinstance(r, dict):
            continue
        rels.append(
            {
                "left_source": r.get("left_source"),
                "right_source": r.get("right_source"),
                "relationship": r.get("relationship"),
                "key_candidates": [
                    {
                        "left_column": k.get("left_column"),
                        "right_column": k.get("right_column"),
                        "confidence": k.get("confidence"),
                    }
                    for k in (r.get("key_candidates") or [])[:3]
                    if isinstance(k, dict)
                ],
            }
        )
    return {"file_profiles": profiles, "relationships": rels}


def _compact_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    out: dict[str, Any] = {}
    if result.get("columns") is not None:
        out["columns"] = list(result.get("columns") or [])
    if result.get("row_count") is not None:
        out["row_count"] = result.get("row_count")
    if result.get("sample_rows") is not None:
        out["sample_rows"] = list(result.get("sample_rows") or [])[:5]
    return out or None


def build_verifier_payload(
    *,
    user_prompt: str,
    plan: dict[str, Any] | None,
    result: dict[str, Any] | None = None,
    understanding: dict[str, Any] | None = None,
    variant: str = "V2",
    independent: bool = True,
    source_schemas: dict[str, list[str]] | None = None,
    materialization_mode: str = "none",
    separate_claims: bool | None = None,
) -> dict[str, Any]:
    """Build golden-free verifier input. variant: V1 | V2 | V3.

    Phase 39B: independent=True (default) splits plan_structure vs planner_claims
    so narrative labels are not mixed into structural evidence.

    Phase 39D/39F/39H materialization_mode:
      - none: baseline (V0)
      - final_schema: final columns only (39D V1)
      - final_schema_origins: final + column origins (39F V2)
      - final_schema_expr: V2 + metric expression ancestry (39H V2.1)
      - final_schema_expr_partition: V2.1 + partition/filter ancestry (39H V2.2)
      - lineage_origins: final + step/events + origins (39F V3)
      - full_lineage: richest deterministic lineage (39F V4)
      - lineage / lineage_claims_separated: legacy 39D V2/V3 aliases
    """
    if separate_claims is None:
        separate_claims = independent
    if independent or separate_claims:
        payload: dict[str, Any] = {
            "user_prompt": user_prompt,
            "plan_structure": _plan_structure_only(plan),
            "planner_claims": _planner_claims(plan),
        }
    else:
        payload = {
            "user_prompt": user_prompt,
            "integration_plan": _compact_plan(plan),
        }

    mode = (materialization_mode or "none").strip().lower()
    # Phase 39F/39H ablation modes:
    #   none / final_schema (V1) / final_schema_origins (V2)
    #   final_schema_expr (V2.1) / final_schema_expr_partition (V2.2)
    #   lineage_origins (V3) / full_lineage (V4)
    # legacy aliases: lineage, lineage_claims_separated
    if mode not in {
        "none",
        "final_schema",
        "final_schema_origins",
        "final_schema_expr",
        "final_schema_expr_partition",
        "lineage",
        "lineage_origins",
        "lineage_claims_separated",
        "full_lineage",
    }:
        mode = "none"
    if mode != "none":
        from core.integrate.schema_lineage import (
            build_schema_lineage,
            extract_source_schemas_from_understanding,
        )

        schemas = source_schemas or extract_source_schemas_from_understanding(
            understanding
        )
        # Without source schemas, lineage is not grounded — omit rather than
        # invent empty step schemas (preserves Phase 39C offline behavior).
        if schemas:
            include_intermediates = mode in {
                "lineage",
                "lineage_origins",
                "lineage_claims_separated",
                "full_lineage",
            }
            evidence = build_schema_lineage(
                plan, schemas, include_intermediates=include_intermediates
            )

            def _strip_filters(sigs: dict[str, Any]) -> dict[str, Any]:
                """V2.1: expression ancestry without partition/filter detail."""
                out: dict[str, Any] = {}
                for col, sig in (sigs or {}).items():
                    if not isinstance(sig, dict):
                        continue
                    s = dict(sig)
                    pop = dict(s.get("row_population") or {})
                    pop["filters"] = []
                    s["row_population"] = pop
                    out[col] = s
                return out

            def _equiv_from_sigs(sigs: dict[str, Any]) -> list[dict[str, Any]]:
                groups: dict[str, list[str]] = {}
                for col, sig in (sigs or {}).items():
                    key = json.dumps(sig, sort_keys=True, ensure_ascii=False, default=str)
                    groups.setdefault(key, []).append(col)
                return [
                    {"evidence_signature": json.loads(k), "final_columns": cols}
                    for k, cols in sorted(groups.items(), key=lambda kv: kv[0])
                    if len(cols) >= 2
                ]

            if mode == "final_schema":
                # V1 — Phase 39D default
                payload["materialization_evidence"] = {
                    "final_schema": evidence.get("final_schema") or [],
                    "unresolved_column_refs": evidence.get(
                        "unresolved_column_refs"
                    )
                    or [],
                    "claimed_columns_absent_from_final": evidence.get(
                        "claimed_columns_absent_from_final"
                    )
                    or [],
                    "notes": evidence.get("notes") or [],
                }
            elif mode == "final_schema_origins":
                # V2 — final schema + source-aware final column origins
                payload["materialization_evidence"] = {
                    "final_schema": evidence.get("final_schema") or [],
                    "final_column_origins": evidence.get("final_column_origins")
                    or {},
                    "source_files_represented_in_final": evidence.get(
                        "source_files_represented_in_final"
                    )
                    or [],
                    "shared_singleton_origin_groups": evidence.get(
                        "shared_singleton_origin_groups"
                    )
                    or [],
                    "unresolved_column_refs": evidence.get(
                        "unresolved_column_refs"
                    )
                    or [],
                    "claimed_columns_absent_from_final": evidence.get(
                        "claimed_columns_absent_from_final"
                    )
                    or [],
                    "notes": evidence.get("notes") or [],
                }
            elif mode == "final_schema_expr":
                # V2.1 — origins + expression ancestry (filters stripped)
                sigs = _strip_filters(
                    evidence.get("final_column_evidence_signatures") or {}
                )
                equiv = _equiv_from_sigs(sigs)
                payload["materialization_evidence"] = {
                    "final_schema": evidence.get("final_schema") or [],
                    "final_column_origins": evidence.get("final_column_origins")
                    or {},
                    "final_column_evidence_signatures": sigs,
                    "equivalent_evidence_signature_groups": equiv,
                    "identical_evidence_signature_column_sets": [
                        list(g.get("final_columns") or []) for g in equiv
                    ],
                    "source_files_represented_in_final": evidence.get(
                        "source_files_represented_in_final"
                    )
                    or [],
                    "shared_singleton_origin_groups": evidence.get(
                        "shared_singleton_origin_groups"
                    )
                    or [],
                    "unresolved_column_refs": evidence.get(
                        "unresolved_column_refs"
                    )
                    or [],
                    "claimed_columns_absent_from_final": evidence.get(
                        "claimed_columns_absent_from_final"
                    )
                    or [],
                    "notes": evidence.get("notes") or [],
                }
            elif mode == "final_schema_expr_partition":
                # V2.2 — expression + partition/filter ancestry (Phase 39H default candidate)
                payload["materialization_evidence"] = {
                    "final_schema": evidence.get("final_schema") or [],
                    "final_column_origins": evidence.get("final_column_origins")
                    or {},
                    "final_column_evidence_signatures": evidence.get(
                        "final_column_evidence_signatures"
                    )
                    or {},
                    "equivalent_evidence_signature_groups": evidence.get(
                        "equivalent_evidence_signature_groups"
                    )
                    or [],
                    "identical_evidence_signature_column_sets": evidence.get(
                        "identical_evidence_signature_column_sets"
                    )
                    or [
                        list(g.get("final_columns") or [])
                        for g in (
                            evidence.get("equivalent_evidence_signature_groups")
                            or []
                        )
                    ],
                    "source_files_represented_in_final": evidence.get(
                        "source_files_represented_in_final"
                    )
                    or [],
                    "shared_singleton_origin_groups": evidence.get(
                        "shared_singleton_origin_groups"
                    )
                    or [],
                    "unresolved_column_refs": evidence.get(
                        "unresolved_column_refs"
                    )
                    or [],
                    "claimed_columns_absent_from_final": evidence.get(
                        "claimed_columns_absent_from_final"
                    )
                    or [],
                    "notes": evidence.get("notes") or [],
                }
            elif mode == "lineage_origins":
                # V3 — final + rename/join structural events + origins
                payload["materialization_evidence"] = {
                    "final_schema": evidence.get("final_schema") or [],
                    "final_column_origins": evidence.get("final_column_origins")
                    or {},
                    "final_column_evidence_signatures": evidence.get(
                        "final_column_evidence_signatures"
                    )
                    or {},
                    "equivalent_evidence_signature_groups": evidence.get(
                        "equivalent_evidence_signature_groups"
                    )
                    or [],
                    "source_files_represented_in_final": evidence.get(
                        "source_files_represented_in_final"
                    )
                    or [],
                    "shared_singleton_origin_groups": evidence.get(
                        "shared_singleton_origin_groups"
                    )
                    or [],
                    "step_outputs": evidence.get("step_outputs") or {},
                    "structural_events": evidence.get("structural_events") or [],
                    "unresolved_column_refs": evidence.get(
                        "unresolved_column_refs"
                    )
                    or [],
                    "claimed_columns_absent_from_final": evidence.get(
                        "claimed_columns_absent_from_final"
                    )
                    or [],
                    "notes": evidence.get("notes") or [],
                }
            elif mode == "full_lineage":
                # V4 — richest deterministic lineage
                payload["materialization_evidence"] = evidence
            else:
                # lineage / lineage_claims_separated (legacy 39D V2/V3)
                payload["materialization_evidence"] = evidence
            if mode in {"lineage_claims_separated", "full_lineage"}:
                payload["planner_claims_authority"] = (
                    "NON_AUTHORITATIVE — claims may be aspirational; "
                    "only materialization_evidence proves column existence."
                )

    if variant in {"V2", "V3"}:
        payload["observed_result"] = _compact_result(result)
    if variant == "V3":
        payload["cross_file_understanding"] = _compact_understanding(understanding)
    return payload


def _banned_keys_present(obj: Any) -> list[str]:
    banned = {
        "overall_ok",
        "expected_grain",
        "expected_operations",
        "golden",
        "scenario",
        "case_id",
        "failure_categories",
        "semantic_equivalent",
        "expected_columns",
        "required_columns_expected",
    }
    found: list[str] = []

    def walk(x: Any, path: str = "") -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                lk = str(k).lower()
                if lk in banned or "golden" in lk or lk.startswith("expected_"):
                    # allow final_output_requirements.required_columns (planner declared)
                    if path.endswith("final_output_requirements") and lk == "required_columns":
                        walk(v, path + "." + str(k))
                        continue
                    if lk == "required_columns" and "final_output_requirements" in path:
                        walk(v, path + "." + str(k))
                        continue
                    found.append(f"{path}.{k}" if path else str(k))
                walk(v, path + "." + str(k) if path else str(k))
        elif isinstance(x, list):
            for i, v in enumerate(x[:20]):
                walk(v, f"{path}[{i}]")

    walk(obj)
    return found


def assert_no_golden_leakage(payload: dict[str, Any]) -> None:
    hits = _banned_keys_present(payload)
    # Filter false positives: planner required_columns is allowed
    hits = [
        h
        for h in hits
        if "final_output_requirements.required_columns" not in h
        and not h.endswith("integration_plan.final_output_requirements.required_columns")
        and not h.endswith("planner_claims.final_output_requirements.required_columns")
    ]
    if hits:
        raise ValueError(f"golden/label leakage in verifier payload: {hits[:10]}")


def _normalize_verdict(raw: dict[str, Any]) -> SemanticVerificationResult:
    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        return SemanticVerificationResult(
            verdict="parse_failed",
            reason_code=None,
            evidence=[],
            raw=raw,
            parse_ok=False,
            error=f"invalid verdict {verdict!r}",
        )
    reason = str(raw.get("reason_code") or "other").strip().lower()
    if reason not in REASON_CODES:
        reason = "other"
    evidence = raw.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = [str(evidence)]
    return SemanticVerificationResult(
        verdict=verdict,
        reason_code=reason,
        evidence=[str(x) for x in evidence][:8],
        raw=raw,
        parse_ok=True,
    )


def run_semantic_verification(
    *,
    user_prompt: str,
    plan: dict[str, Any] | None,
    result: dict[str, Any] | None = None,
    understanding: dict[str, Any] | None = None,
    variant: str = "V2",
    model: str = "qwen2.5:7b",
    base_url: str = "http://localhost:11434",
    chat_json_fn=None,
    independent: bool = True,
    source_schemas: dict[str, list[str]] | None = None,
    materialization_mode: str = "final_schema_expr_partition",
) -> SemanticVerificationResult:
    """Offline research entry. Never mutates plan/result.

    independent=True (Phase 39B default): split plan_structure vs planner_claims.
    materialization_mode default (Phase 39H): final_schema_expr_partition
    (expression + partition ancestry; Python exposes signatures, LLM judges).
    """
    import time

    payload = build_verifier_payload(
        user_prompt=user_prompt,
        plan=plan,
        result=result,
        understanding=understanding,
        variant=variant,
        independent=independent,
        source_schemas=source_schemas,
        materialization_mode=materialization_mode,
    )
    assert_no_golden_leakage(payload)
    user_prefix = (
        "Determine whether the proposed integration plan"
        + (" and observed result" if variant != "V1" else "")
        + " directly satisfy all material requirements in the user's request.\n"
        "Step order (mandatory):\n"
        "  (1) Reconstruct material requirements from user_prompt only.\n"
        "  (2) Decide from plan_structure + materialization_evidence (if present)\n"
        "      whether those requirements are actually materialized.\n"
        "  (3) Optionally glance at planner_claims — never as proof of success.\n"
        "If materialization_evidence lists unresolved_column_refs or\n"
        "claimed_columns_absent_from_final for columns needed by the request,\n"
        "do not treat those claimed metrics as present.\n"
        "If join (without collapsing aggregate) retains side-specific columns\n"
        "in final_schema, treat that as preserved distinction — not collapse.\n"
        "Renamed distinct metric columns present in final_schema also preserve\n"
        "distinction; _left/_right suffixes are not required.\n"
        "Entity grain (one row per key) with multiple side metrics is not collapse.\n"
        "If two side metrics have DIFFERENT evidence_signatures (especially\n"
        "different filters/partitions), treat them as independent sides even when\n"
        "they share a source file — one row carrying both columns is NOT collapse.\n"
        "Prefer materialization_evidence over narrative collapse claims when\n"
        "final_schema retains required sides.\n"
        "CRITICAL: if the request needs independent comparison sides and\n"
        "identical_evidence_signature_column_sets (or\n"
        "equivalent_evidence_signature_groups) puts those side metrics in one\n"
        "set, return fail — same expression over the same row population is not\n"
        "two sides. Names/roles/suffixes do not create independence.\n"
        "Different evidence_signatures (different filters/partitions or different\n"
        "source lineages) may be independent even with overlapping origins.\n"
        "Do not fail merely because sides share a source file when filters differ.\n"
        "If the request needs multiple distinct sides observable, and the ops\n"
        "collapse them into one total before contrast is possible, return fail.\n"
        "If the request only asks to combine/stack tables or compute an overall\n"
        "total across inputs, do not invent a contrast requirement.\n"
        "Do not repair the plan. If evidence is insufficient, return uncertain.\n\n"
    )
    user = user_prefix + json.dumps(payload, ensure_ascii=False, indent=2)

    # Phase 39L: capture exact pre-invocation input (observation only).
    from core.integrate.verifier_invocation_capture import (
        attach_response,
        build_invocation_record,
        capture_enabled,
        env_case_id,
        env_request_id,
        persist_record,
    )

    capture_rec = None
    if capture_enabled():
        capture_rec = build_invocation_record(
            verbatim_user_message=user,
            system_prompt=_VERIFIER_SYSTEM,
            user_instruction_prefix=user_prefix,
            structured_payload=payload,
            model=model,
            base_url=base_url,
            timeout_s=300,
            temperature=0.0,
            format_json=True,
            materialization_mode=materialization_mode,
            variant=variant,
            independent=independent,
            result_provided=result is not None,
            request_id=env_request_id(),
            case_id=env_case_id(),
            chat_path=(
                "injected_chat_json_fn"
                if chat_json_fn is not None
                else "core.llm_client._chat_raw+extract"
            ),
        )

    fn = chat_json_fn or chat_json
    t0 = time.time()
    raw_text: str | None = None
    raw: dict[str, Any] | None = None
    try:
        # Prefer raw-text path for capture fidelity when using default client.
        if capture_rec is not None and chat_json_fn is None:
            from core.llm_client import _chat_raw, _extract_json_object

            raw_text = _chat_raw(
                user,
                system=_VERIFIER_SYSTEM,
                base_url=base_url,
                model=model,
                timeout=300,
                format_json=True,
            )
            raw = _extract_json_object(raw_text)
        else:
            raw = fn(
                user,
                system=_VERIFIER_SYSTEM,
                base_url=base_url,
                model=model,
            )
            if isinstance(raw, dict):
                raw_text = json.dumps(raw, ensure_ascii=False)
        out = _normalize_verdict(raw if isinstance(raw, dict) else {})
    except Exception as exc:  # noqa: BLE001
        out = SemanticVerificationResult(
            verdict="parse_failed",
            parse_ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        if capture_rec is not None:
            capture_rec = attach_response(
                capture_rec,
                raw_text=raw_text,
                raw_parsed=raw if isinstance(raw, dict) else None,
                parsed_verdict=out.verdict,
                parsed_reason_code=out.reason_code,
                parsed_evidence=list(out.evidence or []),
                parse_ok=False,
                parse_error=out.error,
                latency_s=round(time.time() - t0, 3),
            )
            persist_record(capture_rec)
            out.elapsed_s = capture_rec["latency_s"]
            out.model = model
            out.variant = variant
            return out

    out.elapsed_s = round(time.time() - t0, 2)
    out.model = model
    out.variant = variant

    if capture_rec is not None:
        capture_rec = attach_response(
            capture_rec,
            raw_text=raw_text,
            raw_parsed=raw if isinstance(raw, dict) else None,
            parsed_verdict=out.verdict,
            parsed_reason_code=out.reason_code,
            parsed_evidence=list(out.evidence or []),
            parse_ok=out.parse_ok,
            parse_error=out.error,
            latency_s=out.elapsed_s,
        )
        persist_record(capture_rec)
    return out


# Re-export system for tests / artifacts
VERIFIER_SYSTEM_PROMPT = _VERIFIER_SYSTEM
