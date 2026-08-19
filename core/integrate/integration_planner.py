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

# Phase 32: baseline prompt frozen for comparison (do not edit in place for A/B).
_PLANNER_SYSTEM_BASELINE = """
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

Final-output-aware planning (CRITICAL — requirements before steps):
Mentally complete this order BEFORE constructing steps
(do not print chain-of-thought — only emit the JSON plan):
  1) What should ONE ROW in the final output represent?
     (see grain / one_row_represents below)
  2) Which fields must be observable in the final answer?
  3) Where can each required field originate (source / rename / join / aggregate)?
  4) Which transformations are actually necessary for that grain and those fields?
  5) Will any transformation destroy the required grain or fields?
  6) Only then construct the forward step sequence.
Do NOT invent steps first and then invent requirements that merely describe those steps.
Never reverse-engineer requirements from an already-chosen aggregate/select.
Requirements describe the user's requested final result; steps must satisfy them.

Grain = "what does one output row represent?" (Planner judgment — not keyword rules):
- detail: one source/event/transaction row (or one matched combined row) still at
  record grain after connect/link/attach/stack.
- entity: one identifiable entity instance (often after enrichment), still not
  collapsed into group totals.
- group: one aggregated group after collapsing multiple rows by group_by keys.
- summary: one overall collapsed summary (often empty group_by).
Generic illustrations (NOT domain rules): one matched composite-key row; one
enriched lookup row; one category total; one stacked event row.
Connecting / linking / attaching attributes without asking for totals or
"by group" summaries → detail or entity. Do NOT choose group/summary merely
because numeric columns exist.
If tempted to aggregate after a connect/link/attach request, stop: use join
(or union) only and set grain=detail|entity.

final_output_requirements contract:
{
  "grain": "detail|entity|group|summary",
  "one_row_represents": "<short phrase: what one final row means>",
  "required_columns": ["<observed column names needed in the final answer>"]
}
- Prefer always setting one_row_represents (brief).
- For detail/entity after a join: required_columns SHOULD include the join key
  column(s) that identify each output row PLUS attributes needed to answer.
- For group/summary: required_columns SHOULD include group_by fields the user
  needs to read (prefer descriptive name fields present after joins when the
  user asks for named entities) PLUS metric aliases.
- Use only observed/intermediate column names — never invent columns.

Backward dependency reasoning (Planner judgment — not Python rules):
- For each required final field: where does it originate, which step creates/preserves it,
  and does it still exist on final_output?
- For the declared grain: which step establishes it, and does any later step destroy it?
- Preserve fields needed by later joins and by the final output until they are used.

Minimum necessary transformation:
- Every operation must have a necessary role in producing the declared final output.
- Before adding aggregate / select_columns / rename_columns, ask:
  "What information does this step add that is required by the user's requested final output?"
  If none, omit it.

Aggregate necessity (Planner self-check — Python will not delete aggregates):
- Aggregate only when the requested final grain requires collapsing multiple rows
  into a group/summary result.
- What duplication/grain problem does aggregation solve?
- Which final fields will aggregation remove, and are any of them required?
- Is the current dataset already at the requested grain?
- Do not aggregate merely because numeric columns exist.
- If a join/union result already satisfies the declared final output, stop — no aggregate.

Select necessity / FINAL PROJECTION:
- Do not use select_columns merely to make the result look smaller/cleaner.
- Do not add select_columns unless it serves the requested final output.
- Before selecting: are all declared required_columns retained?
- Does select remove identity/context fields needed to interpret each row?
- Are selected names taken from the correct post-join/post-rename schema
  (including suffixes)?
- After enrichment joins, prefer omitting select_columns; if selecting, keep
  join keys and attached attributes required in the answer.
- Are future join keys still needed?

Rename / dirty representation:
- If schemas are largely the same meaning but column names/representations differ,
  rename_columns to align names BEFORE union_rows under aligned policy.
- Do not skip rename when aligned union would otherwise be schema-incompatible.

Relationship evidence is evidence, not an instruction:
- Do not force join/union when relationship evidence is unrelated / insufficient
  and no requested integration is semantically supported → status=cannot_plan.
- join_candidate / compatible_schema hints do not override an unsupported request.
- Conversely: when observations show supported join/lookup/master-detail candidates
  AND the user asks to connect/enrich/calculate across those files, do NOT return
  cannot_plan merely to be "safe". Build the minimum join/union chain that the
  evidence supports. Multi-file enrichment + summary is planned with joins (then
  aggregate only if the final grain requires it), not cannot_plan.

Composition decision guide (domain-neutral; not keyword rules):
- Compatible rows into one dataset → consider union_rows (rename first if needed).
- Summarize after combining → union_rows then aggregate.
- Filter before combining → filter_rows then union_rows then aggregate when totals asked.
- Attributes from another dataset before aggregation → join then aggregate.
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
8) Enrichment join already has all needed row-level fields → join only
   (do not select away keys/fields required in the final answer)
9) Compatible rows with different column spellings → rename then union_rows
10) Multi-file enrichment then totals by named entity → joins then aggregate;
    group_by/required_columns include readable entity fields present after joins

FINAL OUTPUT CONSISTENCY CHECK (before returning status=planned):
FINAL PROJECTION CHECK:
  - Does final_output still expose every field needed to answer the user?
  - Is select_columns actually necessary?
  - Does aggregate collapse fields still required in the answer?
  - Are suffix/rename effects accounted for?
For each declared required column:
  - Where does it come from?
  - Which step creates or preserves it?
  - Does it exist in the final output?
For the declared grain / one_row_represents:
  - Which step establishes the final grain?
  - Does any later operation destroy it?
For every aggregate/select:
  - Why is this operation necessary?
  - Does it remove something required?
For multi-file chains:
  - Are future join keys preserved until used?
If any answer is unresolved: revise the plan before returning it.
Also verify:
- Every necessary source appears in the dependency chain.
- Every intermediate output is consumed correctly.
- Downstream column references exist in prior step schemas (aliases/suffixes).
- Composite relationships include all required key components.
- Every step has non-empty id, op, inputs, output, params.
- Declare final_output_requirements (grain, one_row_represents, required_columns
  using observed names). Do not invent columns.
- If the plan aggregates, set grain to group or summary — never detail/entity.
- If the plan keeps row-level join/union output, set grain to detail or entity
  and list the row-level fields still required in required_columns
  (including identifying join keys for entity/detail enrichment).

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
    "one_row_represents": "<short phrase>",
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


def _build_candidate_a_prompt(baseline: str) -> str:
    """Phase 32 Candidate A: sharpen answer-field vs mechanics + completeness.

    Does NOT add scenario/domain/column hardcoding. Compact delta only.
    """
    anchor = (
        "Do NOT invent steps first and then invent requirements that merely "
        "describe those steps.\n"
        "Never reverse-engineer requirements from an already-chosen aggregate/select.\n"
        "Requirements describe the user's requested final result; steps must satisfy them."
    )
    insert = """Do NOT invent steps first and then invent requirements that merely describe those steps.
Never reverse-engineer requirements from an already-chosen aggregate/select.
Requirements describe the user's requested final result; steps must satisfy them.

Answer fields vs mechanics (CRITICAL — declaration precision):
- required_columns = fields a reader must SEE in the final table to answer the user
  (requested identities, dimensions, labels, and metrics), using observed names only.
- A column used only as a join/link/filter key mid-pipeline is NOT automatically a
  required_column unless the user also needs that column in the final answer.
- Fields the user must read in the answer MUST be declared even when they are not
  join keys.
- Choose the observed column that best matches what the user asked to see; do not
  substitute a different identifier merely because it was convenient for joining.
- Do NOT dump every available column into required_columns — declare a complete but
  minimal answer set."""
    if anchor not in baseline:
        raise RuntimeError("Phase 32 prompt anchor missing from baseline")
    out = baseline.replace(anchor, insert, 1)

    check_anchor = (
        "FINAL OUTPUT CONSISTENCY CHECK (before returning status=planned):\n"
        "FINAL PROJECTION CHECK:"
    )
    check_insert = (
        "FINAL OUTPUT CONSISTENCY CHECK (before returning status=planned):\n"
        "ANSWER COMPLETENESS (declaration):\n"
        "  - Using ONLY declared required_columns on final_output, could a reader\n"
        "    answer the user request? If not, revise required_columns and the steps\n"
        "    that produce/preserve them — before inventing extra unrelated columns.\n"
        "FINAL PROJECTION CHECK:"
    )
    if check_anchor not in out:
        raise RuntimeError("Phase 32 consistency-check anchor missing")
    return out.replace(check_anchor, check_insert, 1)


_PLANNER_SYSTEM_CANDIDATE_A = _build_candidate_a_prompt(_PLANNER_SYSTEM_BASELINE)

# Production prompt remains baseline until Phase 32 adopts Candidate A.
_PLANNER_SYSTEM = _PLANNER_SYSTEM_BASELINE


def get_planner_system_prompt(*, variant: str | None = None) -> str:
    """Return planner system prompt. variant: None/production | baseline | candidate_a."""
    if variant in (None, "production"):
        return _PLANNER_SYSTEM
    if variant == "baseline":
        return _PLANNER_SYSTEM_BASELINE
    if variant == "candidate_a":
        return _PLANNER_SYSTEM_CANDIDATE_A
    raise ValueError(f"unknown planner prompt variant: {variant!r}")


def planner_prompt_token_estimate(text: str | None = None) -> dict[str, int]:
    """Rough token estimate for length audits."""
    src = text if text is not None else _PLANNER_SYSTEM
    words = len(src.split())
    chars = len(src)
    return {
        "chars": chars,
        "words": words,
        "approx_tokens": int(words * 1.3) + chars // 20,
    }


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
    system_prompt: str | None = None,
) -> IntegrationPlan:
    """LLM Integration Planner entry. Format retry only — no semantic repair.

    ``retry_feedback`` (Phase 18/21) appends prior plan/result validation evidence.
    Does not prescribe keys/ops.
    ``system_prompt`` overrides the production planner system prompt (experiments).
    """
    fn = chat_json_fn or chat_json
    system = system_prompt if system_prompt is not None else _PLANNER_SYSTEM
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
                system=system,
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
