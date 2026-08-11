# Phase 14 — Cross-file Data Understanding

> **구현 Phase** (join/union/IntegrationPlan 실행 없음)  
> 근거: Phase 13 `phase13_multifile_architecture.md` + repository 실측

---

## 1. Goal

여러 Excel 파일에 대해:

1. 파일별 deterministic profile
2. 파일 쌍 pairwise observations
3. LLM CrossFileRelationship

을 만들고, **합치는 방법(operation)은 결정하지 않는다.**

질문: *"각 파일은 어떤 구조이고, 서로 어떤 관계일 가능성이 있는가?"*

---

## 2. Architecture

```text
Multiple Excel Files
        ↓
build_file_profile()          [deterministic]
        ↓
build_pairwise_observation()  [deterministic]
        ↓
infer_cross_file_relationship() [LLM semantic]
        ↓
CrossFileUnderstanding
  - file_profiles[]
  - pairwise_observations[]
  - relationships[]
```

Entry: `build_cross_file_understanding(named_frames, ...)`

모듈:

| 파일 | 역할 |
|---|---|
| `core/integrate/relationship_types.py` | contracts |
| `core/integrate/relationship_profile.py` | profiles + pairwise obs |
| `core/integrate/relationship_infer.py` | LLM + package builder |

---

## 3. Reused Existing Components

| 재사용 | 용도 |
|---|---|
| `schema_infer._jsonable`, `_mostly_numeric` | column fact helpers |
| `column_match_key` / `normalize_text` | name normalization |
| inventory 형태 (row/column counts, samples) | FileProfile observations |

**재사용하지 않음 (semantic decision 혼합):**

- `_sanitize_schema_against_frame` numeric→additive
- `_guess_identifier_columns` as PK truth
- `split_sources_and_examples` filename heuristics
- `infer_common_keys` auto join key
- `infer_file_schema` LLM+sanitize path (Phase 14 file profile은 deterministic only)

---

## 4. File Profile Contract

```json
{
  "source_id": "customers",
  "row_count": 3,
  "column_count": 2,
  "observations": {
    "columns": [
      {
        "name": "customer_id",
        "dtype_family": "string",
        "null_ratio": 0.0,
        "uniqueness_ratio": 1.0,
        "distinct_count": 3,
        "sample_values": ["C1", "C2", "C3"]
      }
    ],
    "column_names": ["..."],
    "normalized_column_names": {"...": "..."},
    "numeric_like_columns": [],
    "string_like_columns": []
  },
  "semantic_hints": {}
}
```

분리:

- `observations` = Python facts
- `semantic_hints` = optional non-authoritative (default empty; never auto-filled as PK/additive)

---

## 5. Pairwise Observation Contract

```json
{
  "left_source": "customers",
  "right_source": "orders",
  "schema_similarity": 0.2,
  "exact_column_name_overlap": ["customer_id"],
  "dtype_compatibility_ratio": 1.0,
  "candidate_pairs": [
    {
      "left_column": "customer_id",
      "right_column": "customer_id",
      "dtype_compatible": true,
      "name_similarity": 1.0,
      "left_uniqueness": 1.0,
      "right_uniqueness": 0.75,
      "value_overlap_ratio": 1.0,
      "cardinality_evidence": "one_to_many"
    }
  ],
  "notes": [
    "candidate_pairs are observation pre-filters only — not confirmed join keys"
  ]
}
```

`cardinality_evidence`는 overlap 값의 다중성 패턴 관측이며 **join 결정이 아님**.

---

## 6. Candidate Pair Strategy

목표: Cartesian explosion 방지 + recall 유지.

Keep if:

1. exact or normalized name match, OR
2. dtype-compatible AND name_similarity ≥ 0.28, OR
3. small cartesian (≤80) AND dtype-compatible AND name_sim ≥ 0.15

Then rank by exact/norm match + overlap + name_sim, cap **16**.

**제외 ≠ join key 아님.** LLM은 pruned pair를 못 볼 수 있으므로 threshold를 공격적으로 낮추지 않음.

Dirty overlap: `_overlap_token`이 `"001"` / `1` / `" 001 "`를 동일 토큰으로 측정 (관측용 정규화).

---

## 7. CrossFileRelationship Contract

```json
{
  "left_source": "customers",
  "right_source": "orders",
  "relationship": "master_detail_candidate",
  "key_candidates": [
    {"left_column": "customer_id", "right_column": "customer_id", "confidence": 0.9, "why": "..."}
  ],
  "confidence": 0.88,
  "evidence": ["cardinality_evidence=one_to_many"],
  "ambiguities": [],
  "notes": []
}
```

Vocabulary:

```text
same_schema | compatible_schema | join_candidate | master_detail_candidate |
lookup_candidate | partial_overlap | unrelated | ambiguous | insufficient_evidence
```

- `confidence`: optional LLM self-assessment (Python이 조작하지 않음)
- `evidence`: 짧은 관측 인용만 (긴 CoT 저장 안 함)
- `key_candidates`: relationship 내부 (Phase 15가 바로 소비)
- **no** `recommended_operation` / `steps` / `plan`

Parse failure → retry 1 → `insufficient_evidence` (semantic guess 금지).

---

## 8. LLM Prompt / Responsibility

System prompt 강제:

- observations = facts, hints = non-authoritative
- no integration ops
- no filename semantics
- no numeric→additive
- no uniqueness→confirmed PK
- no name-equality alone→confirmed join
- ambiguous/unrelated/insufficient_evidence allowed

---

## 9. Python vs LLM Boundary

| Python (OK) | LLM (OK) | Forbidden for Python |
|---|---|---|
| dtype, null, uniqueness | relationship label | semantic PK |
| value overlap | key_candidates ranking | join/union/aggregate decision |
| schema similarity | ambiguities | additive measure truth |
| cardinality_evidence pattern | confidence (optional) | filename→role |

---

## 10. Ambiguity / Safe Failure

| Case | Expected |
|---|---|
| employees × sensor | low schema/overlap → LLM `unrelated` / `insufficient_evidence` |
| sales.id × survey.id (no value overlap) | candidate kept by name; overlap=0 → must not confident join |
| multiple strong pairs | candidates preserved → LLM `ambiguous` |
| parse failure | `insufficient_evidence` |

---

## 11. Tests

`tests/test_phase14_cross_file_understanding.py` — 15 tests:

- file profile observation-only
- same schema / master-detail / lookup / unrelated / same-name-diff-meaning
- ambiguous keys preserved
- dirty value overlap
- LLM mock: same_schema, unrelated, ambiguous, invalid vocab→insufficient
- full package + observations-only mode

---

## 12. Regression Result

```text
337 passed, 1 skipped
```

(Phase 12: 322 passed → +15 Phase 14 tests; no regressions.)

---

## 13. Files Changed

```text
core/integrate/relationship_types.py      (new)
core/integrate/relationship_profile.py    (new)
core/integrate/relationship_infer.py      (new)
core/integrate/__init__.py                (exports)
tests/test_phase14_cross_file_understanding.py (new)
docs/learning_note/phase14_cross_file_understanding.md (this)
```

Not changed: route_multi, plan_engine, AnalysisPlan, aggregate_merge removal, UI merge.

---

## 14. Known Limitations

- File-level LLM semantic understanding은 Phase 14에서 생략 (deterministic profile only). 기존 `infer_file_schema`는 integrate path에 그대로 존재.
- Candidate pruning으로 약한 이름/타입 불일치 pair는 LLM에 안 보일 수 있음.
- `cardinality_evidence`는 overlap subset 기준; 전체 키 공간의 복잡한 FK는 LLM 판단 필요.
- Live Ollama quality는 unit mock 범위 밖 (Phase 19 benchmark).

---

## 15. Phase 15 Handoff

Integration Planner가 바로 쓸 수 있는 입력:

```text
CrossFileUnderstanding.to_dict()
  ├── file_profiles[].observations
  ├── pairwise_observations[].candidate_pairs (+ schema_similarity)
  └── relationships[] (semantic labels + key_candidates + ambiguities)
```

Phase 15는 이 위에서 **IntegrationPlan steps** (`union_rows` / `join` / `aggregate` / safe failure)를 결정한다.  
Phase 14 output에는 operation이 없다.
