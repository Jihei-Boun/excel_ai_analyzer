"""Phase 14: LLM Cross-file Relationship Understanding.

Consumes deterministic PairwiseObservation (+ FileProfiles).
Produces CrossFileRelationship without integration operations.
Parsing failure → retry once → explicit insufficient_evidence (no semantic guess).
"""

from __future__ import annotations

import json
from typing import Any, Callable

import pandas as pd

from core.integrate.relationship_profile import (
    build_all_pairwise_observations,
    build_file_profile,
)
from core.integrate.relationship_types import (
    RELATIONSHIP_VOCABULARY,
    CrossFileRelationship,
    CrossFileUnderstanding,
    FileProfile,
    KeyCandidate,
    PairwiseObservation,
)
from core.llm_client import chat_json

_RELATIONSHIP_SYSTEM = """
You analyze relationships between tabular Excel files.
You receive DETERMINISTIC OBSERVATIONS (facts) and optional SEMANTIC HINTS (non-authoritative).

Your job: judge possible cross-file relationships.
You must NOT decide integration operations (no join/union/aggregate/merge plans).
You must NOT invent columns.
You must NOT treat filename/source_id as semantic evidence.
You must NOT treat numeric dtype alone as additive/measure meaning.
You must NOT treat high uniqueness alone as a confirmed primary key.
You must NOT treat column-name equality alone as a confirmed join relationship.

Ambiguity means multiple materially plausible semantic integration interpretations —
NOT merely "columns overlap" or "scores are similar when both are weak".

Label meanings (CRITICAL):
- join_candidate / master_detail_candidate / lookup_candidate:
  mean "join MAY be plausible", NOT "you must join" and NOT a chosen key.
- same_schema / compatible_schema:
  mean schemas align for possible row stacking; NOT "you must union".
  Prefer these when schema_similarity is high and shared columns look like
  the same row layout (including measure columns), unless clear master-detail
  / lookup cardinality exists on a dominant key.
- ambiguous:
  use when TWO OR MORE singleton key candidates are each strong AND near-tied
  (see key_ambiguity_observation.near_tied == true), so choosing one is unresolved.
  Do NOT label ambiguous when near_tied is false.
  Do NOT label ambiguous merely because many columns share names.
- insufficient_evidence: candidates are weak / unclear — not the same as strong near-tie.
- Do NOT emit join_candidate when near_tied singleton keys remain unresolved.
- Composite keys (multiple columns together) are NOT ambiguous singleton choice.
  If composite_key_observations show constituents that are not individually unique
  but the combination is strong, note that in notes — do not call it ambiguous
  solely for that reason.

If evidence is weak, conflicting, or insufficient, choose:
  unrelated | ambiguous | insufficient_evidence

Allowed relationship values:
  same_schema, compatible_schema, join_candidate, master_detail_candidate,
  lookup_candidate, partial_overlap, unrelated, ambiguous, insufficient_evidence

Return ONLY a JSON object with keys:
  relationship (string),
  key_candidates (array of {left_column, right_column, confidence?, why?}),
  confidence (number 0..1 or null),
  evidence (short string array; cite observations, not long reasoning),
  ambiguities (string array),
  notes (string array)
""".strip()


def infer_cross_file_relationship(
    observation: PairwiseObservation,
    *,
    left_profile: FileProfile | None = None,
    right_profile: FileProfile | None = None,
    base_url: str = "http://localhost:11434",
    model: str = "qwen2.5:7b",
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
    max_parse_retries: int = 1,
) -> CrossFileRelationship:
    """LLM semantic relationship for one file pair."""
    fn = chat_json_fn or chat_json
    user = _build_user_prompt(observation, left_profile, right_profile)
    last_error: str | None = None
    for attempt in range(max_parse_retries + 1):
        try:
            prompt = user
            if attempt > 0 and last_error:
                prompt = (
                    f"{user}\n\nPrevious response was invalid: {last_error}\n"
                    "Return a valid JSON object only. If unsure, use "
                    "relationship=insufficient_evidence."
                )
            data = fn(
                prompt,
                system=_RELATIONSHIP_SYSTEM,
                base_url=base_url,
                model=model,
            )
            return _parse_relationship(
                data,
                left_source=observation.left_source,
                right_source=observation.right_source,
            )
        except Exception as exc:  # noqa: BLE001 — parse/LLM failure → retry/fail
            last_error = f"{type(exc).__name__}: {exc}"

    return CrossFileRelationship(
        left_source=observation.left_source,
        right_source=observation.right_source,
        relationship="insufficient_evidence",
        key_candidates=[],
        confidence=None,
        evidence=[],
        ambiguities=["llm_parse_or_call_failed"],
        notes=[last_error or "relationship inference failed"],
    )


def build_cross_file_understanding(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    base_url: str = "http://localhost:11434",
    model: str = "qwen2.5:7b",
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
    infer_relationships: bool = True,
    max_candidates: int = 16,
) -> CrossFileUnderstanding:
    """Full Phase 14 package: profiles + pairwise observations + relationships.

    Does not execute join/union/aggregate and does not build IntegrationPlan.
    Filename/source_id is used only as an identifier string.
    """
    if len(named_frames) < 1:
        return CrossFileUnderstanding(meta={"error": "no_frames"})

    profiles = [
        build_file_profile(str(name), df) for name, df in named_frames
    ]
    profile_map = {p.source_id: p for p in profiles}
    pairwise = build_all_pairwise_observations(
        [(str(n), df) for n, df in named_frames],
        profiles=profile_map,
        max_candidates=max_candidates,
    )

    relationships: list[CrossFileRelationship] = []
    if infer_relationships and pairwise:
        for obs in pairwise:
            relationships.append(
                infer_cross_file_relationship(
                    obs,
                    left_profile=profile_map.get(obs.left_source),
                    right_profile=profile_map.get(obs.right_source),
                    base_url=base_url,
                    model=model,
                    chat_json_fn=chat_json_fn,
                )
            )

    return CrossFileUnderstanding(
        file_profiles=profiles,
        pairwise_observations=pairwise,
        relationships=relationships,
        meta={
            "phase": 14,
            "infer_relationships": infer_relationships,
            "source_count": len(named_frames),
            # Explicit: no operations decided here
            "integration_operations": None,
        },
    )


def _build_user_prompt(
    observation: PairwiseObservation,
    left_profile: FileProfile | None,
    right_profile: FileProfile | None,
) -> str:
    parts = [
        "Deterministic pairwise observations (facts only):\n"
        f"{json.dumps(observation.to_dict(), ensure_ascii=False, indent=2)}",
    ]
    if left_profile is not None:
        parts.append(
            "Left file profile — observations are facts; semantic_hints are NON-AUTHORITATIVE:\n"
            f"{json.dumps(left_profile.to_dict(), ensure_ascii=False, indent=2)}"
        )
    if right_profile is not None:
        parts.append(
            "Right file profile — observations are facts; semantic_hints are NON-AUTHORITATIVE:\n"
            f"{json.dumps(right_profile.to_dict(), ensure_ascii=False, indent=2)}"
        )
    parts.append(
        "Judge the relationship. Do not choose an integration operation. "
        "If multiple key pairs look similarly plausible, set relationship=ambiguous "
        "and list them under ambiguities / key_candidates."
    )
    return "\n\n".join(parts)


def _parse_relationship(
    data: dict[str, Any],
    *,
    left_source: str,
    right_source: str,
) -> CrossFileRelationship:
    if not isinstance(data, dict):
        raise ValueError("relationship response is not an object")

    rel = str(data.get("relationship") or "").strip().lower()
    if rel not in RELATIONSHIP_VOCABULARY:
        raise ValueError(f"invalid relationship value: {rel!r}")

    # Reject accidental operation fields if model invents them
    for banned in (
        "recommended_operation",
        "operation",
        "integration_operation",
        "plan",
        "steps",
    ):
        if banned in data and data.get(banned) not in (None, "", [], {}):
            # Do not use them; record note only
            pass

    keys_raw = data.get("key_candidates") or []
    if keys_raw is None:
        keys_raw = []
    if not isinstance(keys_raw, list):
        raise ValueError("key_candidates must be a list")

    key_candidates: list[KeyCandidate] = []
    for item in keys_raw:
        if not isinstance(item, dict):
            continue
        left_col = str(item.get("left_column") or "").strip()
        right_col = str(item.get("right_column") or "").strip()
        if not left_col or not right_col:
            continue
        conf = item.get("confidence")
        conf_f: float | None
        try:
            conf_f = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf_f = None
        key_candidates.append(
            KeyCandidate(
                left_column=left_col,
                right_column=right_col,
                confidence=conf_f,
                why=str(item.get("why") or ""),
            )
        )

    confidence = data.get("confidence")
    try:
        confidence_f = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence_f = None

    def _str_list(value: Any) -> list[str]:
        if not value:
            return []
        if isinstance(value, list):
            return [str(x) for x in value if str(x).strip()]
        return [str(value)]

    notes = _str_list(data.get("notes"))
    # Strip any operation-like content from notes for safety signal in tests
    for banned_key in ("recommended_operation", "operation", "integration_operation"):
        if banned_key in data and data.get(banned_key) not in (None, "", [], {}):
            notes.append(f"ignored_field:{banned_key}")

    return CrossFileRelationship(
        left_source=left_source,
        right_source=right_source,
        relationship=rel,
        key_candidates=key_candidates,
        confidence=confidence_f,
        evidence=_str_list(data.get("evidence"))[:12],
        ambiguities=_str_list(data.get("ambiguities"))[:12],
        notes=notes[:12],
    )
