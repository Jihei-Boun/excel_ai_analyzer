"""Phase 33: offline semantic wrong-success verification (research only).

Judges whether a successful IntegrationPlan/result satisfies the user request.
Does NOT mutate plans, execute repairs, wire into production pipeline, or
use golden/scenario labels as verifier input.
"""

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
) -> dict[str, Any]:
    """Build golden-free verifier input. variant: V1 | V2 | V3."""
    payload: dict[str, Any] = {
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
) -> SemanticVerificationResult:
    """Offline research entry. Never mutates plan/result."""
    import time

    payload = build_verifier_payload(
        user_prompt=user_prompt,
        plan=plan,
        result=result,
        understanding=understanding,
        variant=variant,
    )
    assert_no_golden_leakage(payload)
    user = (
        "Determine whether the proposed integration plan"
        + (" and observed result" if variant != "V1" else "")
        + " directly satisfy all material requirements in the user's request.\n"
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
