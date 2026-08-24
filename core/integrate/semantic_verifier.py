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
) -> dict[str, Any]:
    """Build golden-free verifier input. variant: V1 | V2 | V3.

    Phase 39B: independent=True (default) splits plan_structure vs planner_claims
    so narrative labels are not mixed into structural evidence.
    """
    if independent:
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
) -> SemanticVerificationResult:
    """Offline research entry. Never mutates plan/result.

    independent=True (Phase 39B default): split plan_structure vs planner_claims.
    """
    import time

    payload = build_verifier_payload(
        user_prompt=user_prompt,
        plan=plan,
        result=result,
        understanding=understanding,
        variant=variant,
        independent=independent,
    )
    assert_no_golden_leakage(payload)
    user = (
        "Determine whether the proposed integration plan"
        + (" and observed result" if variant != "V1" else "")
        + " directly satisfy all material requirements in the user's request.\n"
        "Step order (mandatory):\n"
        "  (1) Reconstruct material requirements from user_prompt only.\n"
        "  (2) Decide from plan_structure whether those requirements survive.\n"
        "  (3) Optionally glance at planner_claims — never as proof of success.\n"
        "If the request needs multiple distinct sides observable, and the ops\n"
        "collapse them into one total before contrast is possible, return fail.\n"
        "If the request only asks to combine/stack tables or compute an overall\n"
        "total across inputs, do not invent a contrast requirement.\n"
        "Do not repair the plan. If evidence is insufficient, return uncertain.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    fn = chat_json_fn or chat_json
    t0 = time.time()
    try:
        raw = fn(
            user,
            system=_VERIFIER_SYSTEM,
            base_url=base_url,
            model=model,
        )
        out = _normalize_verdict(raw if isinstance(raw, dict) else {})
    except Exception as exc:  # noqa: BLE001
        out = SemanticVerificationResult(
            verdict="parse_failed",
            parse_ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    out.elapsed_s = round(time.time() - t0, 2)
    out.model = model
    out.variant = variant
    return out


# Re-export system for tests / artifacts
VERIFIER_SYSTEM_PROMPT = _VERIFIER_SYSTEM
