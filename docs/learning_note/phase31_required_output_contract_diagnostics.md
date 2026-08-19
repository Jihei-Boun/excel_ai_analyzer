# Phase 31 — Required Output Contract Diagnostics

## 1. Executive Summary

Type-B residual(`three_file_chain_001`)의 본질은 **DSL 표현력 부족이 아니라 기존 `required_columns` contract에 대한 Planner under-declaration**이다.  
`customer_name`은 이미 `required_columns`로 선언 가능하고, 32B는 안정적으로 선언·성공하는 반면 7B는 `customer_id`만 선언하거나 cannot_plan한다.  
Python은 undeclared user intent를 추론할 수 없으므로 **새 contract 확장보다 planner declaration 개선(Phase 32A)**이 우선이다. Type C는 범위 밖.

## 2. Type-B Failure Trace

User prompt: `상품 정보를 주문에 연결하고 고객별 카테고리 주문금액을 계산해줘`

| Stage | Content |
|-------|---------|
| Diagnostic required components | `customer_name`, `category_name`, metric total |
| Typical 7B declaration | `required_columns=[customer_id, category_name, total_order_amount]`, grain=`group` |
| Missing declaration | `customer_name` (existing contract can express it) |
| Plan consequence | `group_by` uses `customer_id`; customers join may be unused or unused for name |
| Final consequence | benchmark `missing_structural_columns` |
| Why validators passed | only declared set is checked; undeclared fields are invisible |

First loss point: **Planner final_output_requirements / aggregate group_by** choose surrogate id over descriptive name.

Classification: **Case A — existing_contract_omission** (not expressiveness gap, not pure Type C).

## 3. Existing Contract Audit

| Contract | Declared? | Validated? | Result Checked? | Expresses |
|----------|-----------|------------|-----------------|-----------|
| required_columns | yes | presence ERROR | presence | must-exist final columns (declared set only) |
| grain | yes | consistency (P30 row+collapse ERROR) | weak/info | row identity / collapse |
| one_row_represents | yes | info only | no | free-text self-check — **underused**, not deterministically validatable |
| select / group_by / aliases | yes | yes | yes | projection / collapsed keys / metrics |
| join keys / sources | yes | structural | partial | linkage, not user-facing field completeness |
| required source contribution | **no** | — | — | missing concept; **not required** to explain Type-B |

`required_columns` meaning: **hard presence check for Planner-declared columns**, not a completeness guarantee vs user intent.

## 4. Under-declaration Taxonomy

1. **`required_field_under_declaration`** — primary Type-B family  
2. **`required_source_omission_consequence`** — secondary effect when omitted fields live only on unused sources  
3. **`declared_collapsed_grain_wrong_user_intent`** — Type C (out of scope)

Counts: existing_contract_omission=1, missing_contract_concept=0, fundamentally_semantic=0.

## 5. Planner Declaration Probe

### Frozen Phase 27 corpus (Type-B case)

| Model | Accuracy vs structural label | Under-decl % | Over-decl % |
|-------|-----------------------------:|-------------:|------------:|
| qwen2.5:7b | 0 | 100 | 100* |
| qwen3:32b | 100 | 0 | 100* |

\*Over vs minimal structural label (extras like `customer_id`/`region`) — **currently non-blocking**.

### Focused live probe (unchanged production prompt)

- **7B Type-B:** `cannot_plan` (cannot_determine) in this probe run  
- **32B Type-B:** declares `customer_name` (+ extras), ops=`join×2→aggregate`, under=[]  
- Controls: 7B sometimes under-declares (lookup/same_schema); often over-declares extras without FP blocking  
- 32B controls: structural covered + over-declaration extras

Interpretation: **required fields are planner-declarable; 32B reliable, 7B not.**

## 6. Candidate Contract Matrix

| Candidate | Verdict |
|-----------|---------|
| A Stronger use of required_columns | **recommended** |
| B Activate one_row_represents | **reject** (not deterministic) |
| C Required source contribution | **promising but risky** (over-declare / all-files heuristic risk; unnecessary if A works) |
| D New projection contract | **reject** (duplicates required_columns) |
| E Field survival | **needs more evidence** (mostly already covered for declared fields) |

Golden-independent + deterministically validatable candidates: presence/lineage checks for **declared** contracts only. Completeness vs undeclared intent is **not** golden-independent.

## 7. Escalation Trigger Audit

Current triggers (3):

```text
join_key_dropped_in_final_projection
required_field_not_materializable
final_grain_contradiction
```

Still evidence-based recoverability allowlist — not scenario routing.  
Risk: per-error-code growth → implicit router.  
Future refactor: family flag `recoverable_final_contract_evidence`.  
**Phase 31 added no triggers.**

## 8. Architecture Audit

| Check | Result |
|-------|--------|
| scenario / domain / column hardcoding | PASS |
| semantic autocomplete | PASS |
| Plan mutation / Validator auto-repair / Executor inference | PASS |
| evaluator relaxation | PASS |
| production behavior change | PASS (diagnostic only) |

## 9. Regression

- pytest: all passed (586+)
- deterministic: **100 / 100 / 0**
- Phase 30 baseline frozen: overall **89.47**, safe **96.49**, unsafe **0**, 32B **17.54%**, latency ≈ **34s**

## 10. Recommendation

### **A. Existing contract sufficient — improve planner declaration**

근거:

1. Type B = existing `required_columns` under-declaration  
2. 32B already declares the missing fields reliably  
3. Python cannot validate undeclared intent without semantic inference / golden  
4. New DSL fields would only help if 7B can declare them — evidence says 7B struggles with the *existing* field  
5. Source-contribution extension is not minimal / not necessary yet  

**Phase 32 candidate:** Planner Output-Contract Declaration Improvement (prompt/self-check), validated offline for Type-B lift vs valid-case over-declaration / latency.  
Do **not** jump to Semantic Verification solely for Type-B; Type C remains separate (31B later).

## Artifacts

```text
benchmark_results/multi/phase31/
  baseline_freeze.json
  type_b_failure_traces.json
  output_contract_audit.json
  under_declaration_taxonomy.json
  planner_declaration_probe.json
  candidate_contract_matrix.json
  escalation_trigger_audit.json
  phase31_kpis.json
```
