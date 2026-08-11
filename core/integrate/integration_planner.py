"""Phase 15: LLM Integration Planner (plan only — no execution / semantic repair).

Input: CrossFileUnderstanding + user_prompt
Output: IntegrationPlan (planned | cannot_plan)

Format retry only on parse/shape failure. No relationship→op mapping.
No key_candidates[0] injection. No numeric→sum. No aggregate_merge rewrite.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from core.integrate.integration_plan_types import (
    INTEGRATION_ATOMIC_OPS,
    IntegrationPlan,
    IntegrationPlanParseError,
    canonical_integration_plan_signature,
    integration_operation_family_signature,
    integration_plan_from_dict,
)
from core.integrate.relationship_types import CrossFileUnderstanding
from core.llm_client import chat_json

_PLANNER_SYSTEM = """
You are an Integration Planner for a generic multi-file Excel analyzer.
You receive:
  1) user_prompt (what the user wants)
  2) CrossFileUnderstanding: deterministic observations + semantic relationship labels

Your job: produce ONE JSON IntegrationPlan using atomic steps only.
You do NOT execute joins/unions/aggregates. You only plan.

Allowed ops ONLY:
  rename_columns, filter_rows, union_rows, join, aggregate, select_columns

Do NOT use aggregate_merge, pivot, pandas code, or invented ops.

Relationship labels are HINTS, never operation orders:
- join_candidate / master_detail_candidate / lookup_candidate ≠ "must join"
- same_schema / compatible_schema ≠ "must union"
- Always combine user_prompt + observations + labels.

Key selection:
- key_candidates are candidates, not confirmed truth.
- If key_ambiguity_observation.near_tied is true (multiple singleton keys with
  near-equal evidence) and the user did not resolve which key/link to use:
  return status=cannot_plan (do NOT pick one key arbitrarily).
- Composite keys (multiple columns together) differ from ambiguous singleton choice.

Composition decision guide (domain-neutral; not keyword rules):
- Rows from multiple compatible datasets into one dataset → consider union_rows.
- Summarize after combining rows → often union_rows then aggregate.
- Filter before combining → filter_rows (per source as needed) then union_rows
  then aggregate when totals are requested.
- Attributes from another dataset needed before aggregation → join then aggregate.
- Multiple reference datasets → multiple joins may be required before aggregate.
- Prefer explicit intermediate outputs; final_output must be a step output id.

Abstract structure examples (NOT domain instructions):
1) Compatible event rows in A and B; user wants total metric by entity across both:
   union_rows → aggregate
2) Detail rows need attributes from a reference table, then totals by group:
   join → aggregate
3) Two near-tied singleton keys and user only says "connect the files":
   cannot_plan

Other rules:
- Do not invent sources/columns. Use source_id and observed column names only.
- Do not use filename semantics as meaning.
- Do not assume numeric columns are additive without user intent + evidence.
- Prefer a minimal step list. No unnecessary steps.
- If unrelated / ambiguous keys / insufficient evidence / unsupported transform:
  return status=cannot_plan with reason and ambiguities (steps must be []).

JSON shape:
{
  "status": "planned" | "cannot_plan",
  "steps": [
    {
      "id": "step_1",
      "op": "<op>",
      "inputs": ["..."],
      "output": "...",
      "params": { ... }
    }
  ],
  "final_output": "<output id or null>",
  "reason": "<string or null>",
  "ambiguities": ["..."],
  "notes": ["..."]
}

Op params:
- rename_columns: { "mapping": {"old":"new"} }
- filter_rows: { "conditions": [ ... ] }
  literal: {"column","operator","value"}
  column-vs-column: {"left_column","operator","right_column"}
    (or {"column","operator","right_column"})
  NEVER put a column name in value to mean column-vs-column — use right_column.
  operators: eq|ne|gt|gte|lt|lte
- union_rows: { "column_policy": "aligned" }  (optional; default aligned)
- join: { "left_keys":[...], "right_keys":[...], "how":"inner|left|right|outer" }
  inputs MUST be exactly [left_source, right_source] in that order
- aggregate: { "group_by":[...], "metrics":[{"column","function","alias?"}] }
  functions: sum|mean|median|min|max|count
- select_columns: { "columns":[...] }
""".strip()


def build_integration_plan(
    user_prompt: str,
    understanding: CrossFileUnderstanding | dict[str, Any],
    *,
    base_url: str = "http://localhost:11434",
    model: str = "qwen2.5:7b",
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
    max_parse_retries: int = 1,
    retry_feedback: list[str] | None = None,
) -> IntegrationPlan:
    """LLM Integration Planner entry. Format retry only — no semantic repair.

    ``retry_feedback`` (Phase 18) appends prior plan/result validation evidence.
    Does not prescribe keys/ops.
    """
    fn = chat_json_fn or chat_json
    understanding_dict = (
        understanding.to_dict()
        if isinstance(understanding, CrossFileUnderstanding)
        else dict(understanding)
    )
    compact = _compact_understanding_for_prompt(understanding_dict)
    user = _build_user_prompt(user_prompt, compact, retry_feedback=retry_feedback)

    last_error: str | None = None
    for attempt in range(max_parse_retries + 1):
        prompt = user
        if attempt > 0 and last_error:
            prompt = (
                f"{user}\n\nPrevious plan failed structural parsing: {last_error}\n"
                "Fix JSON shape/types only. Do not invent columns/keys/metrics. "
                "If still unsure, return status=cannot_plan with empty steps."
            )
        try:
            data = fn(
                prompt,
                system=_PLANNER_SYSTEM,
                base_url=base_url,
                model=model,
            )
            plan = integration_plan_from_dict(data)
            plan.meta = {
                **dict(plan.meta),
                "parse_attempts": attempt + 1,
                "plan_signature": canonical_integration_plan_signature(plan),
                "operation_family": integration_operation_family_signature(plan),
                "allowed_ops": sorted(INTEGRATION_ATOMIC_OPS),
            }
            return plan
        except IntegrationPlanParseError as exc:
            last_error = str(exc)
        except Exception as exc:  # noqa: BLE001 — LLM/JSON failure
            last_error = f"{type(exc).__name__}: {exc}"

    return IntegrationPlan(
        status="cannot_plan",
        steps=[],
        final_output=None,
        reason="planner_parse_failed",
        ambiguities=["planner_error"],
        notes=[last_error or "unknown parse failure"],
        meta={
            "phase": 15,
            "parse_attempts": max_parse_retries + 1,
            "planner_error": last_error,
        },
    )


def _compact_understanding_for_prompt(understanding: dict[str, Any]) -> dict[str, Any]:
    """Shrink Phase 14 payload: keep planner-relevant fields, drop long samples."""
    profiles_out: list[dict[str, Any]] = []
    for p in understanding.get("file_profiles") or []:
        if not isinstance(p, dict):
            continue
        obs = dict(p.get("observations") or {})
        columns = []
        for c in obs.get("columns") or []:
            if not isinstance(c, dict):
                continue
            columns.append(
                {
                    "name": c.get("name"),
                    "dtype_family": c.get("dtype_family"),
                    "null_ratio": c.get("null_ratio"),
                    "uniqueness_ratio": c.get("uniqueness_ratio"),
                    "distinct_count": c.get("distinct_count"),
                    "sample_values": list(c.get("sample_values") or [])[:4],
                }
            )
        profiles_out.append(
            {
                "source_id": p.get("source_id"),
                "row_count": p.get("row_count"),
                "column_count": p.get("column_count"),
                "observations": {
                    "columns": columns,
                    "column_names": obs.get("column_names") or [c["name"] for c in columns],
                },
                "semantic_hints": p.get("semantic_hints") or {},
            }
        )

    pairwise_out: list[dict[str, Any]] = []
    for o in understanding.get("pairwise_observations") or []:
        if not isinstance(o, dict):
            continue
        pairs = []
        for pair in (o.get("candidate_pairs") or [])[:10]:
            if not isinstance(pair, dict):
                continue
            pairs.append(
                {
                    "left_column": pair.get("left_column"),
                    "right_column": pair.get("right_column"),
                    "dtype_compatible": pair.get("dtype_compatible"),
                    "name_similarity": pair.get("name_similarity"),
                    "value_overlap_ratio": pair.get("value_overlap_ratio"),
                    "left_uniqueness": pair.get("left_uniqueness"),
                    "right_uniqueness": pair.get("right_uniqueness"),
                    "cardinality_evidence": pair.get("cardinality_evidence"),
                }
            )
        pairwise_out.append(
            {
                "left_source": o.get("left_source"),
                "right_source": o.get("right_source"),
                "schema_similarity": o.get("schema_similarity"),
                "exact_column_name_overlap": o.get("exact_column_name_overlap"),
                "candidate_pairs": pairs,
                "key_ambiguity_observation": o.get("key_ambiguity_observation") or {},
                "composite_key_observations": list(o.get("composite_key_observations") or [])[:4],
            }
        )

    rel_out: list[dict[str, Any]] = []
    for r in understanding.get("relationships") or []:
        if not isinstance(r, dict):
            continue
        rel_out.append(
            {
                "left_source": r.get("left_source"),
                "right_source": r.get("right_source"),
                "relationship": r.get("relationship"),
                "key_candidates": r.get("key_candidates") or [],
                "confidence": r.get("confidence"),
                "evidence": list(r.get("evidence") or [])[:6],
                "ambiguities": list(r.get("ambiguities") or [])[:6],
            }
        )

    return {
        "file_profiles": profiles_out,
        "pairwise_observations": pairwise_out,
        "relationships": rel_out,
    }


def _build_user_prompt(
    user_prompt: str,
    compact_understanding: dict[str, Any],
    *,
    retry_feedback: list[str] | None = None,
) -> str:
    parts = [
        f"User request:\n{user_prompt}\n\n"
        "CrossFileUnderstanding (observations + relationship hints; "
        "relationship≠operation):\n"
        f"{json.dumps(compact_understanding, ensure_ascii=False, indent=2)}\n"
    ]
    if retry_feedback:
        parts.append("\nPrior attempt feedback (evidence only — do not invent keys/ops):\n")
        parts.append("\n".join(retry_feedback))
        parts.append(
            "\nDo not repeat the previous rejected plan or the same integration "
            "strategy family unchanged. "
            "Produce a materially different plan, or status=cannot_plan if ambiguity remains.\n"
        )
    parts.append("\nReturn IntegrationPlan JSON only.")
    return "".join(parts)
