"""Phase 14: Cross-file relationship contracts (observations vs semantic judgments).

Python produces FileProfile / PairwiseObservation (facts).
LLM produces CrossFileRelationship (semantic judgment, no integration ops).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


# LLM vocabulary — relationship ≠ recommended integration operation
RELATIONSHIP_VOCABULARY = frozenset(
    {
        "same_schema",
        "compatible_schema",
        "join_candidate",
        "master_detail_candidate",
        "lookup_candidate",
        "partial_overlap",
        "unrelated",
        "ambiguous",
        "insufficient_evidence",
    }
)

# Observational cardinality patterns (derived from uniqueness among overlap only)
CARDINALITY_EVIDENCE = frozenset(
    {
        "one_to_one",
        "one_to_many",
        "many_to_one",
        "many_to_many",
        "insufficient",
    }
)


@dataclass
class ColumnObservation:
    """Deterministic column facts only."""

    name: str
    dtype_family: str  # numeric | string | datetime | bool | other
    pandas_dtype: str
    null_ratio: float
    uniqueness_ratio: float
    distinct_count: int
    non_null_count: int
    sample_values: list[Any] = field(default_factory=list)
    is_numeric_like: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileProfile:
    """Per-file profile for Phase 15 Planner consumption.

    observations: deterministic Python facts
    semantic_hints: optional non-authoritative hints (never treated as truth)
    """

    source_id: str
    row_count: int
    column_count: int
    observations: dict[str, Any] = field(default_factory=dict)
    semantic_hints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "observations": self.observations,
            "semantic_hints": self.semantic_hints,
        }


@dataclass
class ColumnPairObservation:
    """Deterministic stats for one candidate column pair (not a join decision)."""

    left_column: str
    right_column: str
    dtype_compatible: bool
    left_dtype_family: str
    right_dtype_family: str
    name_exact_match: bool
    name_similarity: float
    left_uniqueness: float
    right_uniqueness: float
    left_null_ratio: float
    right_null_ratio: float
    left_distinct_count: int
    right_distinct_count: int
    value_overlap_ratio: float
    overlap_count: int
    cardinality_evidence: str
    pruned: bool = False
    prune_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PairwiseObservation:
    """Deterministic cross-file observations between two sources."""

    left_source: str
    right_source: str
    left_row_count: int
    right_row_count: int
    left_column_count: int
    right_column_count: int
    exact_column_name_overlap: list[str] = field(default_factory=list)
    normalized_column_name_overlap: list[str] = field(default_factory=list)
    schema_similarity: float = 0.0
    dtype_compatibility_ratio: float = 0.0
    candidate_pairs: list[ColumnPairObservation] = field(default_factory=list)
    # Phase 20: observational key ambiguity / composite uniqueness (not decisions)
    key_ambiguity_observation: dict[str, Any] = field(default_factory=dict)
    composite_key_observations: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_source": self.left_source,
            "right_source": self.right_source,
            "left_row_count": self.left_row_count,
            "right_row_count": self.right_row_count,
            "left_column_count": self.left_column_count,
            "right_column_count": self.right_column_count,
            "exact_column_name_overlap": list(self.exact_column_name_overlap),
            "normalized_column_name_overlap": list(self.normalized_column_name_overlap),
            "schema_similarity": self.schema_similarity,
            "dtype_compatibility_ratio": self.dtype_compatibility_ratio,
            "candidate_pairs": [p.to_dict() for p in self.candidate_pairs],
            "key_ambiguity_observation": dict(self.key_ambiguity_observation),
            "composite_key_observations": list(self.composite_key_observations),
            "notes": list(self.notes),
        }


@dataclass
class KeyCandidate:
    """LLM-proposed key candidate (semantic; not Python-confirmed PK)."""

    left_column: str
    right_column: str
    confidence: float | None = None
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CrossFileRelationship:
    """LLM semantic judgment — must NOT include integration operations."""

    left_source: str
    right_source: str
    relationship: str
    key_candidates: list[KeyCandidate] = field(default_factory=list)
    confidence: float | None = None
    evidence: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_source": self.left_source,
            "right_source": self.right_source,
            "relationship": self.relationship,
            "key_candidates": [k.to_dict() for k in self.key_candidates],
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "ambiguities": list(self.ambiguities),
            "notes": list(self.notes),
        }


@dataclass
class CrossFileUnderstanding:
    """Phase 14 output package for Phase 15 Integration Planner."""

    file_profiles: list[FileProfile] = field(default_factory=list)
    pairwise_observations: list[PairwiseObservation] = field(default_factory=list)
    relationships: list[CrossFileRelationship] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_profiles": [p.to_dict() for p in self.file_profiles],
            "pairwise_observations": [o.to_dict() for o in self.pairwise_observations],
            "relationships": [r.to_dict() for r in self.relationships],
            "meta": dict(self.meta),
        }
