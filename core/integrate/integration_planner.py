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

Key / ambiguity (CRITICAL — do not invent evidence):
- ONLY treat key_ambiguity_observation.near_tied == true as singleton-key ambiguity.
  Do NOT invent near-tie from shared column names, schema overlap, or composite lists.
- If near_tied is true AND the user did not resolve which singleton key/link to use
  AND no composite interpretation is supported by observations + user request:
  return status=cannot_plan (do NOT pick one singleton key arbitrarily).
- If near_tied is false and observations support a clear singleton relationship,
  and the user asks to connect/link datasets, a join on that supported key is
  appropriate — do NOT return cannot_plan merely because the request says "connect".
- Composite vs singleton ambiguity:
  Multiple individually weak key columns may jointly form a stronger relationship.
  If composite_key_observations show a column combination with strong uniqueness
  (especially when constituents_individually_unique is false / composite_improves_uniqueness)
  AND the user request indicates a multi-column link, consider a composite join
  (left_keys/right_keys with multiple columns).
  Do NOT select a composite key merely because multiple columns exist.
  Do NOT use composite join to "resolve" two independent strong singleton keys
  when the user only asks to connect files without multi-column criteria.
- Shared columns under high schema_similarity usually support row stacking
  (union), not forced join-key ambiguity.

Final grain awareness (Planner judgment — not keyword rules):
- Row-level combined data: user wants files combined as rows → often union_rows
  (and rename_columns if representation differs). Do NOT add aggregate unless
  the user asks for totals/summaries by entity/group.
- Entity/group-level summary: user wants totals/sums by key → combine then aggregate.
- Attribute enrichment then summary: join (possibly multiple) then aggregate.
- Before returning, verify that the final output grain matches what the user asked for.
  Do not aggregate merely because numeric columns exist.
  Do not preserve detail rows when the requested answer requires an entity/group-level
  summary. Every aggregate step must be necessary for the requested final result.
- If the user asks only to connect / link / attach / stack rows, stop after the
  join or union that produces that row-level result — do not add aggregate.
- After enrichment joins, prefer omitting select_columns; if selecting, retain join
  keys and attributes still needed to understand the enriched rows.
- When summarizing by an entity, group_by columns should identify that entity using
  columns present after joins (prefer stable identifiers already in the joined
  schema; include descriptive label columns in group_by or select when they remain
  available and the user asked for named entities).

Composition decision guide (domain-neutral; not keyword rules):
- Rows from multiple compatible datasets into one dataset → consider union_rows.
- Summarize after combining rows → often union_rows then aggregate.
- Filter before combining → filter_rows (per source as needed) then union_rows
  then aggregate when totals are requested.
- Attributes from another dataset needed before aggregation → join then aggregate.
- Multiple reference datasets → multiple joins may be required before aggregate.
- Prefer explicit intermediate outputs; final_output must be a step output id.

Aggregate alias contract (CRITICAL):
- Every aggregate metric SHOULD include an explicit "alias" for the output column.
- If the user asks for a named total/summary column, set alias to that name using
  characters available in the request (do not invent domain synonyms).
- Prefer concise aliases aligned with the user request wording. Avoid inventing
  long compound names that merely restate the source column (downstream steps
  must still reference whatever alias you declare).
- If alias is omitted, the structural default output name is the source column name
  (not column__fn, not a translated label). Downstream steps must use that name.
- Later select_columns / filters must reference the declared aggregate alias
  (or the structural default if alias was omitted).

Join suffix contract (structural):
- When non-key columns share the same name on both sides of a join, the output
  uses suffixes _left and _right (e.g. status → status_left / status_right).
- Downstream steps must reference the suffixed names when collisions occurred.
- Key columns used in the join keep their merge naming (same-name keys collapse).

Composition caution:
- If the user only asks to connect/link/attach datasets without requesting
  summaries/totals/group metrics, prefer join (or union when stacking rows)
  without adding aggregate — still emit a planned join/union when evidence supports it.
- Prefer omitting select_columns unless narrowing is required; if used, retain
  keys and attributes still needed for later steps or the final answer.
- Compatible / dirty same-grain row stacking remains union_rows (with rename when
  representation differs), not join+aggregate.

Abstract structure examples (NOT domain instructions):
1) Compatible event rows in A and B; user wants total metric by entity across both:
   union_rows → aggregate (with explicit metric alias)
2) Detail rows need attributes from a reference table, then totals by group:
   join → aggregate
3) Two near-tied singleton keys and user only says "connect the files":
   cannot_plan
4) Individually weak keys with strong composite observation; user asks to link
   using both dimensions → composite join keys (multiple left_keys/right_keys)
5) User only asks to stack compatible rows → union_rows only (no aggregate)
6) User asks to sum/mean a non-numeric string column → cannot_plan
   (do not substitute count)
7) User only asks to connect row-level detail without totals → join only
   (do not add aggregate that drops detail columns)

Before returning status=planned, self-check:
- Determine the grain required by the user's requested final output
  (detail/entity row-level vs group/summary).
- Verify every transformation preserves or intentionally changes grain
  toward that requested output.
- Do not aggregate merely because numeric columns exist.
- If a join/union result already satisfies the requested output, do not add
  an unnecessary aggregate.
- Before select_columns, verify fields needed in the final answer remain available.
  Prefer omitting select_columns unless a clear subset is required.
- For multi-step joins, preserve fields required by later steps and the final output.
- Every aggregate/select step must have a clear purpose for the requested final result.
- Does every necessary source appear in the dependency chain?
- Does every requested transformation have a corresponding step?
- Is each intermediate output consumed correctly?
- Does final_output match the requested grain (row-level vs group-level)?
- Are derived summaries preceded by the required integration step?
- Are aggregate aliases explicit when summaries are requested?
- Does every downstream column reference exist in the declared output schema
  of its previous step (including aggregate aliases and join suffixes)?
- Are all keys needed by later joins/aggregates preserved (not dropped by
  an earlier select_columns)?
- For composite relationships, are all required key components represented?
- Every step has non-empty id, op, inputs, output, params.
- Declare final_output_requirements (grain + required_columns using observed
  column names) so validators can check consistency. Do not invent columns.
- If the plan aggregates, set grain to group or summary — never detail/entity.
- If the plan keeps row-level join/union output, set grain to detail or entity
  and list the row-level fields still required in required_columns.

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
  "final_output_requirements": {
    "grain": "detail|entity|group|summary",
    "required_columns": ["..."]
  },
  "reason": "<string or null>",
  "ambiguities": ["..."],
  "notes": ["..."]
}

Other rules:
- Do not invent sources/columns. Use source_id and observed column names only.
- Do not use filename semantics as meaning.
- Do not assume numeric columns are additive without user intent + evidence.
- If the user asks to sum/mean/median/min/max a column whose observed dtype_family
  is string (non-numeric), return cannot_plan — do NOT silently switch to count
  to force a success.
- Prefer a minimal step list. No unnecessary steps.
- If unrelated / unresolved singleton ambiguity / insufficient evidence /
  unsupported transform: return status=cannot_plan with reason and ambiguities
  (steps must be []).
- Compatible/same-schema row stacking: prefer union_rows (not join) when the user
  asks to combine/stack rows across files with aligned columns.

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
- aggregate: { "group_by":[...], "metrics":[{"column","function","alias"}] }
  functions: sum|mean|median|min|max|count
  Prefer always setting alias.
- select_columns: { "columns":[...] }
""".strip()


def _count_on_string_metrics(
    plan: IntegrationPlan, understanding: dict[str, Any]
) -> list[str]:
    """Detect count metrics on observed string columns (evidence for retry feedback)."""
    dtype_by_col: dict[str, str] = {}
    for p in understanding.get("file_profiles") or []:
        if not isinstance(p, dict):
            continue
        for c in (p.get("observations") or {}).get("columns") or []:
            if isinstance(c, dict) and c.get("name"):
                dtype_by_col[str(c["name"])] = str(c.get("dtype_family") or "")
    hits: list[str] = []
    for step in plan.steps:
        if step.op != "aggregate":
            continue
        for m in step.params.get("metrics") or []:
            if not isinstance(m, dict):
                continue
            if str(m.get("function") or "").lower() != "count":
                continue
            col = str(m.get("column") or "")
            if dtype_by_col.get(col) == "string":
                hits.append(col)
    return hits


def _prompt_requests_additive_aggregation(user_prompt: str) -> bool:
    """Safety heuristic: additive language present (not used to choose ops/keys)."""
    text = str(user_prompt or "").lower()
    hints = (
        "합산",
        "합계",
        "총합",
        "총액",
        " sum",
        "sum ",
        "average",
        "mean",
        "평균",
    )
    return any(h.lower() in text for h in hints) or text.strip().startswith("sum")


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

    ``retry_feedback`` (Phase 18/21) appends prior plan/result validation evidence.
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
    # Extra attempt for count-on-string evidence feedback (does not rewrite the plan).
    total_attempts = max_parse_retries + 1 + 1
    count_string_feedback_used = False
    for attempt in range(total_attempts):
        prompt = user
        if attempt > 0 and last_error:
            prompt = (
                f"{user}\n\nPrevious plan issue: {last_error}\n"
                "Fix JSON shape/types only when the issue is structural parsing. "
                "Do not invent columns/keys/metrics. "
                "Ensure every step has id, op, inputs, output, params. "
                "If additive aggregation on a non-numeric column was requested, "
                "return status=cannot_plan. Do not substitute count to force success. "
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
            # Safety: additive request + count-on-string is silent substitution.
            # Evidence feedback once; if repeated, cannot_plan (do not invent ops).
            additive = _prompt_requests_additive_aggregation(user_prompt)
            if (
                plan.status == "planned"
                and additive
                and not count_string_feedback_used
                and attempt < total_attempts - 1
            ):
                hits = _count_on_string_metrics(plan, understanding_dict)
                if hits:
                    count_string_feedback_used = True
                    last_error = (
                        "Plan uses count on non-numeric (string) column(s): "
                        + ", ".join(hits)
                        + " while the user request indicates additive aggregation. "
                        "Return status=cannot_plan. Do not substitute count to "
                        "force success."
                    )
                    continue
            if plan.status == "planned" and additive and count_string_feedback_used:
                hits = _count_on_string_metrics(plan, understanding_dict)
                if hits:
                    return IntegrationPlan(
                        status="cannot_plan",
                        steps=[],
                        final_output=None,
                        reason="non_numeric_additive_aggregation_unsupported",
                        ambiguities=["non_numeric_metric"],
                        notes=[
                            "Refusing count-on-string substitution for an additive "
                            f"aggregation request on columns {hits}."
                        ],
                        meta={
                            "phase": 21,
                            "parse_attempts": attempt + 1,
                            "count_on_string_refused": True,
                        },
                    )
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
            "parse_attempts": total_attempts,
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
                "normalized_column_name_overlap": o.get("normalized_column_name_overlap"),
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
        parts.append("\n")
    parts.append("\nReturn IntegrationPlan JSON only.")
    return "".join(parts)
