"""Phase 14: Deterministic file profiles and pairwise cross-file observations.

Observation only — never concludes join/union/PK/additive semantics.
Reuses column_match_key / normalize patterns from core/io and inventory shape
from schema_infer.build_frame_inventory (without LLM schema sanitize).
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

import pandas as pd

from core.integrate.relationship_types import (
    CARDINALITY_EVIDENCE,
    ColumnObservation,
    ColumnPairObservation,
    FileProfile,
    PairwiseObservation,
)
from core.integrate.schema_infer import _jsonable, _mostly_numeric
from core.io.normalize import column_match_key
from core.io.text_normalize import normalize_text

# Candidate pruning (recall-oriented; exclusion ≠ "not a join key")
_MAX_CANDIDATE_PAIRS = 16
_NAME_SIM_KEEP = 0.28
_CARTESIAN_SOFT_CAP = 80  # above this, require name_sim or exact/normalized match


def build_file_profile(
    source_id: str,
    df: pd.DataFrame,
    *,
    sample_values: int = 8,
    semantic_hints: dict[str, Any] | None = None,
) -> FileProfile:
    """Build deterministic FileProfile. semantic_hints are optional & non-authoritative."""
    columns: list[dict[str, Any]] = []
    for col in df.columns:
        obs = _column_observation(str(col), df[col], sample_values=sample_values)
        columns.append(obs.to_dict())

    observations = {
        "columns": columns,
        "column_names": [str(c) for c in df.columns],
        "normalized_column_names": {
            str(c): column_match_key(c) for c in df.columns
        },
        "numeric_like_columns": [
            c["name"] for c in columns if c.get("is_numeric_like")
        ],
        "string_like_columns": [
            c["name"] for c in columns if c.get("dtype_family") == "string"
        ],
    }
    hints = dict(semantic_hints or {})
    # Never invent PK/additive truths here — hints must be explicitly passed in.
    return FileProfile(
        source_id=str(source_id),
        row_count=int(len(df)),
        column_count=int(len(df.columns)),
        observations=observations,
        semantic_hints=hints,
    )


def build_pairwise_observation(
    left_source: str,
    left_df: pd.DataFrame,
    right_source: str,
    right_df: pd.DataFrame,
    *,
    left_profile: FileProfile | None = None,
    right_profile: FileProfile | None = None,
    max_candidates: int = _MAX_CANDIDATE_PAIRS,
) -> PairwiseObservation:
    """Deterministic pairwise stats between two frames (no relationship conclusion)."""
    left_profile = left_profile or build_file_profile(left_source, left_df)
    right_profile = right_profile or build_file_profile(right_source, right_df)

    left_names = [str(c) for c in left_df.columns]
    right_names = [str(c) for c in right_df.columns]
    left_norm = {n: column_match_key(n) for n in left_names}
    right_norm = {n: column_match_key(n) for n in right_names}

    exact_overlap = sorted(set(left_names) & set(right_names))
    left_by_norm: dict[str, list[str]] = {}
    for n, k in left_norm.items():
        left_by_norm.setdefault(k, []).append(n)
    right_by_norm: dict[str, list[str]] = {}
    for n, k in right_norm.items():
        right_by_norm.setdefault(k, []).append(n)
    shared_norm_keys = sorted(set(left_by_norm) & set(right_by_norm))
    normalized_overlap = sorted(
        {
            f"{left_by_norm[k][0]}≈{right_by_norm[k][0]}"
            for k in shared_norm_keys
        }
    )

    schema_sim = _schema_similarity(left_names, right_names, left_norm, right_norm)
    dtype_compat_ratio = _dtype_compatibility_ratio(left_df, right_df, left_norm, right_norm)

    candidates = _select_candidate_pairs(
        left_df,
        right_df,
        left_norm=left_norm,
        right_norm=right_norm,
        max_candidates=max_candidates,
    )
    key_ambiguity = _key_ambiguity_observation(candidates)
    composites = _composite_key_observations(
        left_df, right_df, exact_overlap=exact_overlap
    )

    notes = [
        "candidate_pairs are observation pre-filters only — not confirmed join keys",
        "cardinality_evidence is uniqueness pattern among overlapping values — not a join decision",
        "filename/source_id is display-only and must not drive semantics",
        "key_ambiguity_observation / composite_key_observations are facts, not operation choices",
    ]
    return PairwiseObservation(
        left_source=str(left_source),
        right_source=str(right_source),
        left_row_count=int(len(left_df)),
        right_row_count=int(len(right_df)),
        left_column_count=int(len(left_names)),
        right_column_count=int(len(right_names)),
        exact_column_name_overlap=exact_overlap,
        normalized_column_name_overlap=normalized_overlap,
        schema_similarity=round(schema_sim, 4),
        dtype_compatibility_ratio=round(dtype_compat_ratio, 4),
        candidate_pairs=candidates,
        key_ambiguity_observation=key_ambiguity,
        composite_key_observations=composites,
        notes=notes,
    )


def build_all_pairwise_observations(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    profiles: dict[str, FileProfile] | None = None,
    max_candidates: int = _MAX_CANDIDATE_PAIRS,
) -> list[PairwiseObservation]:
    """All unordered pairs among named frames."""
    if len(named_frames) < 2:
        return []
    frame_map = {str(n): df for n, df in named_frames}
    profiles = profiles or {
        str(n): build_file_profile(str(n), df) for n, df in named_frames
    }
    out: list[PairwiseObservation] = []
    for left, right in combinations(sorted(frame_map.keys()), 2):
        out.append(
            build_pairwise_observation(
                left,
                frame_map[left],
                right,
                frame_map[right],
                left_profile=profiles.get(left),
                right_profile=profiles.get(right),
                max_candidates=max_candidates,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _column_observation(
    name: str,
    series: pd.Series,
    *,
    sample_values: int = 8,
) -> ColumnObservation:
    non_null = series.dropna()
    n = int(len(series))
    nn = int(len(non_null))
    distinct = int(non_null.nunique()) if nn else 0
    uniqueness = float(distinct / nn) if nn else 0.0
    null_ratio = float(series.isna().mean()) if n else 0.0
    family = _dtype_family(series)
    numeric_like = bool(
        pd.api.types.is_numeric_dtype(series) or _mostly_numeric(non_null)
    )
    samples = [_jsonable(v) for v in non_null.head(sample_values).tolist()]
    return ColumnObservation(
        name=name,
        dtype_family=family,
        pandas_dtype=str(series.dtype),
        null_ratio=round(null_ratio, 4),
        uniqueness_ratio=round(uniqueness, 4),
        distinct_count=distinct,
        non_null_count=nn,
        sample_values=samples,
        is_numeric_like=numeric_like,
    )


def _dtype_family(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "bool"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_numeric_dtype(series) or _mostly_numeric(series.dropna()):
        return "numeric"
    if pd.api.types.is_string_dtype(series) or series.dtype == object:
        return "string"
    return "other"


def _schema_similarity(
    left_names: list[str],
    right_names: list[str],
    left_norm: dict[str, str],
    right_norm: dict[str, str],
) -> float:
    if not left_names and not right_names:
        return 1.0
    left_set = set(left_norm.values())
    right_set = set(right_norm.values())
    if not left_set and not right_set:
        return 1.0
    inter = len(left_set & right_set)
    union = len(left_set | right_set)
    return float(inter / union) if union else 0.0


def _dtype_compatibility_ratio(
    left_df: pd.DataFrame,
    right_df: pd.DataFrame,
    left_norm: dict[str, str],
    right_norm: dict[str, str],
) -> float:
    """Among normalized-name overlaps, fraction with compatible dtype families."""
    left_by = {v: k for k, v in left_norm.items()}
    right_by = {v: k for k, v in right_norm.items()}
    shared = set(left_by) & set(right_by)
    if not shared:
        return 0.0
    ok = 0
    for key in shared:
        lf = _dtype_family(left_df[left_by[key]])
        rf = _dtype_family(right_df[right_by[key]])
        if _dtypes_compatible(lf, rf):
            ok += 1
    return float(ok / len(shared))


def _dtypes_compatible(left_family: str, right_family: str) -> bool:
    if left_family == right_family:
        return True
    # numeric↔string often share coded ids ("001" vs 1) — compatible for overlap stats
    pair = {left_family, right_family}
    if pair <= {"numeric", "string"}:
        return True
    return False


def _name_similarity(a: str, b: str) -> float:
    """Simple deterministic similarity on normalized names (0..1)."""
    na = column_match_key(a)
    nb = column_match_key(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # token Jaccard + containment
    ta = set(na.replace("-", "_").split("_")) - {""}
    tb = set(nb.replace("-", "_").split("_")) - {""}
    if ta and tb:
        jacc = len(ta & tb) / len(ta | tb)
    else:
        jacc = 0.0
    # character bigram Dice
    def bigrams(s: str) -> set[str]:
        if len(s) < 2:
            return {s}
        return {s[i : i + 2] for i in range(len(s) - 1)}

    ba, bb = bigrams(na), bigrams(nb)
    dice = (2 * len(ba & bb) / (len(ba) + len(bb))) if ba and bb else 0.0
    contain = 0.35 if (na in nb or nb in na) else 0.0
    return float(min(1.0, max(jacc, dice, contain)))


def _overlap_token(value: object) -> str | None:
    """Normalize cell values for overlap measurement only (not semantic identity).

    Handles dirty forms like '001', 1, ' 001 ' → same token when digit-like.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            fv = float(value)
            if pd.isna(fv):
                return None
            if fv.is_integer():
                return str(int(fv))
            return repr(fv)
        except Exception:  # noqa: BLE001
            return str(value).strip()
    text = str(value).strip()
    if not text:
        return None
    # digit-like with optional leading zeros / commas
    compact = text.replace(",", "").replace(" ", "")
    if compact.isdigit() or (compact.startswith("-") and compact[1:].isdigit()):
        try:
            return str(int(compact))
        except Exception:  # noqa: BLE001
            pass
    return normalize_text(text) or text.lower()


def _value_sets(series: pd.Series) -> set[str]:
    tokens: set[str] = set()
    for v in series.dropna().tolist():
        tok = _overlap_token(v)
        if tok is not None:
            tokens.add(tok)
    return tokens


def _value_overlap(
    left: pd.Series,
    right: pd.Series,
) -> tuple[float, int, set[str]]:
    left_set = _value_sets(left)
    right_set = _value_sets(right)
    if not left_set or not right_set:
        return 0.0, 0, set()
    inter = left_set & right_set
    # overlap relative to smaller side (symmetric-ish coverage signal)
    denom = min(len(left_set), len(right_set))
    ratio = float(len(inter) / denom) if denom else 0.0
    return ratio, int(len(inter)), inter


def _cardinality_evidence(
    left: pd.Series,
    right: pd.Series,
    overlap_tokens: set[str],
) -> str:
    """Observational multiplicity pattern among overlapping tokens only."""
    if len(overlap_tokens) < 2:
        return "insufficient"

    def _counts(series: pd.Series) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in series.dropna().tolist():
            tok = _overlap_token(v)
            if tok in overlap_tokens:
                out[tok] = out.get(tok, 0) + 1
        return out

    lc = _counts(left)
    rc = _counts(right)
    if not lc or not rc:
        return "insufficient"

    left_max = max(lc.values())
    right_max = max(rc.values())
    left_uniqueish = left_max <= 1
    right_uniqueish = right_max <= 1

    if left_uniqueish and right_uniqueish:
        pattern = "one_to_one"
    elif left_uniqueish and not right_uniqueish:
        pattern = "one_to_many"
    elif right_uniqueish and not left_uniqueish:
        pattern = "many_to_one"
    else:
        pattern = "many_to_many"
    assert pattern in CARDINALITY_EVIDENCE
    return pattern


def _select_candidate_pairs(
    left_df: pd.DataFrame,
    right_df: pd.DataFrame,
    *,
    left_norm: dict[str, str],
    right_norm: dict[str, str],
    max_candidates: int,
) -> list[ColumnPairObservation]:
    left_cols = [str(c) for c in left_df.columns]
    right_cols = [str(c) for c in right_df.columns]
    cartesian = len(left_cols) * len(right_cols)
    scored: list[tuple[float, ColumnPairObservation]] = []

    for lc in left_cols:
        for rc in right_cols:
            name_exact = lc == rc
            name_sim = 1.0 if name_exact else _name_similarity(lc, rc)
            norm_match = left_norm[lc] == right_norm[rc]
            lf = _dtype_family(left_df[lc])
            rf = _dtype_family(right_df[rc])
            dtype_ok = _dtypes_compatible(lf, rf)

            # Pre-filter: exclusion means "not scored as candidate", NOT "not a key"
            keep = False
            prune_reason = None
            if name_exact or norm_match:
                keep = True
            elif dtype_ok and name_sim >= _NAME_SIM_KEEP:
                keep = True
            elif cartesian <= _CARTESIAN_SOFT_CAP and dtype_ok and name_sim >= 0.15:
                keep = True
            else:
                prune_reason = (
                    "low_name_similarity_or_dtype_mismatch"
                    if not dtype_ok
                    else "name_similarity_below_threshold"
                )

            if not keep:
                continue

            left_obs = _column_observation(lc, left_df[lc])
            right_obs = _column_observation(rc, right_df[rc])
            overlap_ratio, overlap_count, overlap_tokens = _value_overlap(
                left_df[lc], right_df[rc]
            )
            card = _cardinality_evidence(left_df[lc], right_df[rc], overlap_tokens)

            pair = ColumnPairObservation(
                left_column=lc,
                right_column=rc,
                dtype_compatible=dtype_ok,
                left_dtype_family=lf,
                right_dtype_family=rf,
                name_exact_match=name_exact,
                name_similarity=round(name_sim, 4),
                left_uniqueness=left_obs.uniqueness_ratio,
                right_uniqueness=right_obs.uniqueness_ratio,
                left_null_ratio=left_obs.null_ratio,
                right_null_ratio=right_obs.null_ratio,
                left_distinct_count=left_obs.distinct_count,
                right_distinct_count=right_obs.distinct_count,
                value_overlap_ratio=round(overlap_ratio, 4),
                overlap_count=overlap_count,
                cardinality_evidence=card,
                pruned=False,
                prune_reason=prune_reason,
            )
            # Ranking for cap: prefer exact/norm match, then overlap, then name_sim
            score = _candidate_strength_score(pair)
            scored.append((score, pair))

    scored.sort(key=lambda x: (-x[0], x[1].left_column, x[1].right_column))
    return [p for _, p in scored[:max_candidates]]


def _candidate_strength_score(pair: ColumnPairObservation) -> float:
    """Deterministic ranking score for observation only (not a join decision)."""
    return (
        (3.0 if pair.name_exact_match else 0.0)
        + 2.0 * float(pair.value_overlap_ratio)
        + 1.0 * float(pair.name_similarity)
        + (0.3 if pair.dtype_compatible else 0.0)
    )


_NEAR_TIE_GAP = 0.15
_STRONG_SCORE = 4.0  # exact+high overlap roughly
_STRONG_OVERLAP = 0.8
# Singleton join-key plausibility needs uniqueness on at least one side.
# Low-uniqueness overlapping columns (composite parts) are NOT singleton key candidates.
_SINGLETON_UNIQUENESS = 0.95


def _key_ambiguity_observation(
    candidates: list[ColumnPairObservation],
) -> dict[str, Any]:
    """Detect near-tied singleton key candidates (observation — does not choose a key)."""
    strong: list[tuple[float, ColumnPairObservation]] = []
    for p in candidates:
        if float(p.value_overlap_ratio) < _STRONG_OVERLAP:
            continue
        if float(p.name_similarity) < 0.9 and not p.name_exact_match:
            continue
        # Composite-part columns often overlap but are not unique alone.
        if max(float(p.left_uniqueness), float(p.right_uniqueness)) < _SINGLETON_UNIQUENESS:
            continue
        strong.append((_candidate_strength_score(p), p))
    strong.sort(key=lambda x: (-x[0], x[1].left_column, x[1].right_column))
    if len(strong) < 2:
        return {
            "plausible_singleton_count": len(strong),
            "near_tied": False,
            "evidence_gap": None,
            "tied_pairs": [],
        }
    gap = float(strong[0][0] - strong[1][0])
    top = strong[0][0]
    near_tied = gap <= _NEAR_TIE_GAP and strong[1][0] >= _STRONG_SCORE
    tied = [
        {
            "left_column": p.left_column,
            "right_column": p.right_column,
            "score": round(s, 4),
            "value_overlap_ratio": p.value_overlap_ratio,
            "left_uniqueness": p.left_uniqueness,
            "right_uniqueness": p.right_uniqueness,
            "cardinality_evidence": p.cardinality_evidence,
        }
        for s, p in strong
        if abs(s - top) <= _NEAR_TIE_GAP
    ]
    return {
        "plausible_singleton_count": len(strong),
        "near_tied": bool(near_tied and len(tied) >= 2),
        "evidence_gap": round(gap, 4),
        "tied_pairs": tied if near_tied else [],
    }


def _composite_key_observations(
    left_df: pd.DataFrame,
    right_df: pd.DataFrame,
    *,
    exact_overlap: list[str],
) -> list[dict[str, Any]]:
    """Observational composite uniqueness for exact-overlap column pairs (size 2)."""
    cols = [c for c in exact_overlap if c in left_df.columns and c in right_df.columns]
    if len(cols) < 2:
        return []
    out: list[dict[str, Any]] = []
    for combo in combinations(cols[:5], 2):
        left_cols = list(combo)
        right_cols = list(combo)
        left_u = _composite_uniqueness(left_df, left_cols)
        right_u = _composite_uniqueness(right_df, right_cols)
        if left_u >= 0.98 and right_u >= 0.98:
            card = "one_to_one"
        elif left_u >= 0.98 and right_u < 0.98:
            card = "one_to_many"
        elif right_u >= 0.98 and left_u < 0.98:
            card = "many_to_one"
        elif left_u < 0.95 and right_u < 0.95:
            card = "many_to_many"
        else:
            card = "insufficient"
        out.append(
            {
                "left_columns": left_cols,
                "right_columns": right_cols,
                "left_uniqueness": round(left_u, 4),
                "right_uniqueness": round(right_u, 4),
                "cardinality_evidence": card,
            }
        )
    return out[:6]


def _composite_uniqueness(df: pd.DataFrame, cols: list[str]) -> float:
    if not cols or len(df) == 0:
        return 0.0
    n = int(len(df))
    distinct = int(df.groupby(cols, dropna=False).ngroups)
    return float(distinct / n) if n else 0.0

