"""Planner AnalysisPlan contract, decision guide, and minimal few-shots.

도메인 keyword rule이 아니라 분석 형태 + operation schema를 명확히 한다.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# System prompt (contract + decision guide + few compact examples)
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """
You are a planning module for a generic Excel analyzer.
Produce ONE JSON AnalysisPlan for a deterministic executor.
Do NOT write pandas code. Do NOT invent columns that are not in the inventory.
Return ONLY a JSON object.

## Allowed atomic ops
annotate_row_types, filter_rows, select_columns, derive_column, sort, limit,
drop_columns, aggregate, ratio_of_aggregates, compare_groups,
distribution_summary, correlation, filter_vs_mean, top_per_group.

## Compact high-level forms (compiled to atomic steps)
Prefer atomic steps when unsure. These generic sugars are OK:
operation=aggregate | find_items | group_comparison | correlation |
rate_vs_mean | top_n_per_group | top_n_difference | split_by_difference |
filter_vs_mean.
Do NOT invent domain-specific ops (no top_sales, execution_rate_top, etc.).
Legacy aliases like execution_rate_compare compile to the same generic shapes —
prefer group_comparison / rate_vs_mean instead.

## Operation contracts (required fields MUST be present)

### aggregate
required: group_by (array of existing columns), metrics (array of {column, fn})
optional: prefer_subtotals (sum only), include_groups, output_columns, criteria_note, interpret
fn MUST be one of: sum | mean | median | min | max | count (avg→mean). NEVER omit fn.
metrics MUST use shape [{ "column": "<existing>", "fn": "sum" }].
Do NOT invent aliases like sales_sum / 매출액_합계 as column names.
Do NOT use { "매출액_합계": "sum" } — that shape is invalid.
IMPORTANT: after aggregate, the metric column KEEP its source name (e.g. 매출).
Later sort/select/compare MUST use that same name — NEVER invent 매출_합계 / amount_sum / score_mean.

### ratio_of_aggregates
required: name (explicit output id), numerator, denominator (existing columns after aggregate)
Meaning: sum(numerator)/sum(denominator) at group level — NOT mean of row ratios.
Typical pipeline: annotate → filter detail → aggregate → ratio_of_aggregates → sort → limit.
Later sort/compare MUST reference the same `name`. Never omit `name`.

### sort
required: by (array of columns), ascending (bool or bool array)

### limit
required: n (positive int)

### filter_rows
optional: include_row_types, column_filters[{column, values}],
numeric_filters[{column, op, value}] OR [{left_column, op, right_column}]
op: eq|ne|gt|gte|lt|lte

### filter_vs_mean
required: column (existing numeric), relation (above|below)
Use for "above/below the mean of a column" on detail rows.

### rate_vs_mean
required: numerator, denominator, relation (above|below)
optional: rate_name
Use when comparing a derived ratio to its mean.

### compare_groups / group_comparison
required: group_column, groups (explicit category values), and either
  (a) metrics[{column, fn}] for metric comparison, OR
  (b) numerator + denominator for rate comparison
Do NOT use denominator="count" or null to mean average — use aggregate fn=mean instead.
Do NOT use group_comparison for a simple group-mean table; use aggregate with fn=mean.

### find_items
required: numeric_filters OR column_filters that reference existing columns
For label equality use column_filters, not numeric_filters.
Never put mean(...) expressions in value — use filter_vs_mean / rate_vs_mean.

### top_per_group / top_n_per_group
required: group_column, value_column, n
ONLY when each group needs its own top-N rows.
For GLOBAL top-N after totaling a metric: aggregate → sort → limit (NOT top_per_group).

### correlation
required: x_column, y_column

### top_n_difference
required: value_columns with 2 columns for differences; OR 1 column for plain top-N sort→limit

## Analysis-form decision guide (NOT domain keywords)

### Ranking decision tree (critical)

Ask: does the user need ONE global ranking, or a ranking INSIDE EACH group?

Also ask: is the ranking target a **row** or an **entity** that may span many rows?

#### Row ranking (sort the raw rows)
When each ranking item is already one row (order line, sensor reading, invoice id with unique_ratio≈1):
  annotate → filter detail → sort(by=metric) → limit(n)
Do NOT aggregate first.
Do NOT use top_per_group for a single global ranking.
Do NOT use filter_vs_mean to find a max/min.
Do NOT use find_items with op=max/min — use sort → limit.

#### Entity ranking (aggregate then rank)
When the ranked entity can appear on many rows (product/customer/item with low unique_ratio / grain_hint=repeated_entity_candidate):
  aggregate(group_by=entity, metrics=[{column, fn}]) → sort → limit
Examples of shape: "top N products by sales", "top N customers by order amount".
Inventory unique_ratio / grain_hint are hints only — not hard rules.

#### Global top-N / largest / smallest (single overall ranking)
If ranking a RATE/RATIO:
  aggregate → ratio_of_aggregates(name=rate) → sort(by=rate) → limit
Never rank a rate with top_per_group alone. Never omit ratio_of_aggregates.

#### Group-wise top-N (ranking inside each group)
Only when EACH group needs its own top-N members:
  (optional aggregate if totals needed) → top_per_group(group_column, value_column, n)
Examples of shape: "top N products in EACH region", "top 2 people in EACH department".
The presence of a category word alone does NOT imply group-wise ranking.
If the request is one overall ranking, use sort → limit (row) or aggregate→sort→limit (entity).

### Ratio / Rate decision guide
When the analysis needs a relationship between two quantities
(rate / ratio / vs / against / relative to a target or budget):
  aggregate both measures → ratio_of_aggregates with an EXPLICIT name
  Then sort/limit or compare_groups may reference that name.
Do not answer a rate request with aggregate-only (missing ratio).
Do not invent intermediate alias columns; use existing inventory columns.

### Compare decision guide
compare_groups is a LATE step. First create the comparison metric.
Named groups + single metric:
  filter/select groups → aggregate(fn=...) → compare_groups
Named groups + rate:
  aggregate → ratio_of_aggregates(name=rate) → compare_groups(metrics include rate)
Do not start with compare_groups before the metric exists.
Do not replace compare_groups with aggregate → sort → limit when named groups are compared.

#### Group value comparison
When the user names two+ category values on one dimension (e.g. region A vs B):
  use group_column + groups + metric → compare_groups

#### Metric / column comparison
When comparing two numeric measures (sales vs target) for the same rows/entities:
  derive_column or dual metrics — NOT compare_groups with groups=[metric names].

#### Ambiguous semantic comparison
If the request only says "compare performance/results" without naming groups or metrics,
do NOT invent group values. Prefer aggregate by a natural group dimension with interpret=true,
or ask via criteria_note which groups/metrics were chosen. Use role_hints / sibling metrics.
Keep compare_groups only when groups are explicit or clearly implied by schema categories.

### Above / below mean
Row-level metric vs its mean: filter_vs_mean(column, above|below).
Group totals vs mean of totals: aggregate first → filter_vs_mean on the aggregated metric.
Ratio vs its mean: rate_vs_mean (or derive/ratio → filter_vs_mean on the rate name).
Do NOT use filter_vs_mean to compare two different columns (use column-vs-column filters).
Do NOT answer a "rate above/below mean" request with aggregate alone.

### Column-vs-column condition
filter_rows numeric_filters: {left_column, op, right_column}.

### Group average / mean by category
aggregate with metrics:[{column, fn:"mean"}]. Never fake mean via ratio to count.

## Composition contracts (dependencies)

### sort
Target columns MUST already exist: source columns or outputs of prior steps
(aggregate metrics, ratio name, derive name). Sorting a nonexistent `rate` is invalid.

### limit
Use for GLOBAL ranking after sort (or after top_per_group is NOT appropriate).
limit alone without sort/top_per_group is incomplete for ranking.

### top_per_group
required: group_column, value_column, n
ONLY for within-group ranking. Invalid as a substitute for global sort→limit.
Do not combine top_per_group with a separate global limit for the same ranking intent.

### ratio_of_aggregates
required: name (explicit output name), numerator, denominator
Subsequent sort/compare MUST use that same name. Prefer short names like `rate` / `집행률`.

### compare_groups
required: group_column, groups (explicit category values, typically >= 2),
  and metrics that already exist in the plan
If comparing a rate, ratio_of_aggregates must precede compare_groups.
Do NOT use compare_groups to compare two metric columns; that is not a group dimension.

## Ambiguous similarly-named metrics
If several numeric columns share a stem (current/ytd/target/actual), pick the best
match to the request wording using role_hints; state the choice in criteria_note.
Never invent a column.

## Row types
For item rankings and detail filters: annotate_row_types then filter detail rows.
prefer_subtotals applies to sum only.

## Minimal composition examples (reuse patterns; do not memorize phrases)

1) Group aggregate (generic sales-like):
{"operation":"aggregate","group_by":["region"],"metrics":[{"column":"amount","fn":"sum"}],"interpret":false}

2) Ratio + ranking (generic):
{"steps":[
  {"op":"annotate_row_types"},
  {"op":"filter_rows","include_row_types":["detail"]},
  {"op":"aggregate","group_by":["item"],"metrics":[{"column":"executed","fn":"sum"},{"column":"budget","fn":"sum"}]},
  {"op":"ratio_of_aggregates","name":"rate","numerator":"executed","denominator":"budget"},
  {"op":"sort","by":["rate"],"ascending":[false]},
  {"op":"limit","n":3}
],"interpret":false}

3) Group comparison with rate (generic):
{"operation":"group_comparison","group_column":"region","groups":["East","West"],
 "numerator":"sales","denominator":"target","rate_name":"attainment","interpret":true}

4) Column-vs-column filter (generic inventory-like):
{"operation":"find_items",
 "numeric_filters":[{"left_column":"qty","op":"lt","right_column":"min_qty"}],
 "output_columns":["sku","qty","min_qty"],"interpret":false}

5) Global largest row (generic):
{"steps":[
  {"op":"annotate_row_types"},
  {"op":"filter_rows","include_row_types":["detail"]},
  {"op":"sort","by":["amount"],"ascending":[false]},
  {"op":"limit","n":1},
  {"op":"select_columns","columns":["sku","amount"]}
],"interpret":false}

6) Group-wise top-N (generic):
{"operation":"top_n_per_group","group_column":"region","value_column":"amount","n":3,"interpret":false}

7) Above-mean after group totals (generic sales-like):
{"steps":[
  {"op":"aggregate","group_by":["rep"],"metrics":[{"column":"amount","fn":"sum"}]},
  {"op":"filter_vs_mean","column":"amount","relation":"above"}
],"interpret":false}
""".strip()


COMPOSITION_CATEGORIES = (
    "global_ranking_misclassified",
    "group_ranking_misclassified",
    "missing_ratio",
    "wrong_compare_shape",
    "missing_metric_before_sort",
    "misused_top_per_group",
    "unsupported_composition",
    "missing_required_field",
    "duplicate_plan",
    "empty_plan",
    "missing_rate_vs_mean",
    "column_vs_column_failure",
    "aggregate_output_alias",
)

# Field-level fixes: keep composition, fix names/fields
_REPAIR_ISSUE_CODES = frozenset(
    {
        "missing_sort_column",
        "missing_select_column",
        "missing_metric_before_sort",
        "compare_before_metric",
        "missing_ratio_name",
        "missing_aggregation_fn",
        "missing_vs_mean_column",
        "missing_metric_before_filter_vs_mean",
    }
)

# Composition family wrong: full regenerate
_REGENERATE_ISSUE_CODES = frozenset(
    {
        "entity_ranking_missing_aggregate",
        "misused_top_per_group",
        "global_ranking_misclassified",
        "global_ranking_missing_limit",
        "column_vs_column_misclassified",
        "missing_ratio_composition",
        "missing_rate_vs_mean_composition",
        "compare_groups_need_two_groups",
        "empty_plan",
    }
)


def choose_retry_mode(codes: list[str]) -> str:
    """failure codes → repair | regenerate."""
    code_set = {str(c) for c in codes}
    if code_set & _REGENERATE_ISSUE_CODES:
        return "regenerate"
    if code_set & _REPAIR_ISSUE_CODES:
        return "repair"
    return "regenerate"


def retry_invariant_message(codes: list[str], category: str | None = None) -> str:
    """Planner에게 전달할 composition invariant (정답 plan 아님)."""
    joined = " ".join(codes)
    if any(c in joined for c in ("missing_sort_column", "missing_select_column", "missing_metric_before")):
        return (
            "After aggregate(metrics=[{column:X, fn:...}]), later steps must reference column X. "
            "Do not invent X_합계 / X_sum / X_mean output names."
        )
    if "compare_before_metric" in joined or "compare_groups_need_two" in joined:
        return (
            "compare_groups needs an existing metric column name (same as aggregate metric) "
            "and at least two explicit group values."
        )
    if "entity_ranking" in joined:
        return (
            "The ranking target appears in multiple rows. "
            "Aggregate a metric for each entity before sorting and limiting."
        )
    if "column_vs_column" in joined or "missing_vs_mean" in joined:
        return (
            "Row-wise comparison of two numeric columns must use a "
            "column-to-column relationship from the schema, not a single-column "
            "comparison against that column's own statistical mean."
        )
    if "rate_vs_mean" in joined or "missing_ratio" in joined:
        return (
            "Rate vs mean requires a derived rate column first, then filter_vs_mean on that name."
        )
    if "misused_top_per_group" in joined or "global_ranking" in joined:
        return (
            "Global ranking uses metric → sort → limit; "
            "top_per_group is only for within-group ranking."
        )
    if category:
        return f"Satisfy composition category constraints for {category}."
    return "Fix the invalid fields while matching the user request."


def composition_category_from_issues(codes: list[str]) -> str | None:
    """validation issue code → composition failure category."""
    joined = " ".join(codes)
    if "misused_top_per_group" in joined or "conflicting_ranking" in joined:
        return "misused_top_per_group"
    if "missing_rate_vs_mean" in joined:
        return "missing_rate_vs_mean"
    if "column_vs_column" in joined or "missing_vs_mean" in joined:
        return "column_vs_column_failure"
    if "entity_ranking" in joined:
        return "global_ranking_misclassified"
    if "missing_sort_column" in joined or "missing_select_column" in joined:
        return "aggregate_output_alias"
    if "missing_ratio" in joined or "ratio_required" in joined:
        return "missing_ratio"
    if "missing_metric_before" in joined:
        return "missing_metric_before_sort"
    if "compare_metric" in joined or "compare_before_metric" in joined or "compare_groups_need" in joined:
        return "wrong_compare_shape"
    if "global_ranking" in joined:
        return "global_ranking_misclassified"
    if "group_ranking" in joined:
        return "group_ranking_misclassified"
    if "composition" in joined:
        return "unsupported_composition"
    return None


def plan_composition_category(plan_dict: dict | None) -> str:
    """plan 구조에서 composition category 추정 (retry 반복 감지용)."""
    if not isinstance(plan_dict, dict):
        return "empty_plan"
    steps = plan_dict.get("steps") or []
    ops = []
    if isinstance(steps, list):
        for s in steps:
            if isinstance(s, dict):
                ops.append(str(s.get("op") or s.get("operation") or ""))
    op_hl = str(plan_dict.get("operation") or "")
    if op_hl:
        ops.insert(0, op_hl)
    has = set(ops)
    if "top_per_group" in has or "top_n_per_group" in has:
        if "limit" in has or "ratio_of_aggregates" not in has:
            return "misused_top_per_group"
        return "group_ranking_misclassified"
    if "compare_groups" in has or op_hl in {"group_comparison", "compare_groups"}:
        if "ratio_of_aggregates" in has or plan_dict.get("denominator"):
            return "wrong_compare_shape"
        return "wrong_compare_shape"
    if "filter_vs_mean" in has and "aggregate" not in has and "sort" in has:
        return "global_ranking_misclassified"
    if "aggregate" in has and "sort" not in has and "limit" not in has and "ratio_of_aggregates" not in has:
        return "missing_metric_before_sort"
    if "sort" in has and "ratio_of_aggregates" not in has:
        return "missing_metric_before_sort"
    return "unsupported_composition"


def planner_failure_reason(exc: BaseException | str) -> str:
    """plan_build 예외 메시지를 세분 코드로 분류."""
    text = str(exc)
    low = text.lower()
    if "json" in low and ("parse" in low or "decode" in low or "expecting" in low):
        return "invalid_json"
    if "실행 가능한 분석 step이 없습니다" in text or "no executable" in low:
        if "aggregate" in low or "metrics" in text:
            return "wrong_operation_shape"
        if "find_items" in low or "numeric_filters" in text:
            return "wrong_operation_shape"
        if "group_comparison" in low or "denominator" in low:
            return "missing_required_field"
        return "empty_plan"
    if "missing" in low and "fn" in low:
        return "missing_required_field"
    if "unsupported" in low:
        return "unsupported_operation"
    if "column" in low and ("invent" in low or "missing" in low or "없" in text):
        return "invalid_column_reference"
    if "timeout" in low:
        return "planner_timeout"
    if "객체가 아닙니다" in text or "not a dict" in low or "not an object" in low:
        return "invalid_json"
    return "plan_build_error"


def normalize_plan_signature(plan_dict: dict | None) -> str:
    """재시도 동일 plan 감지용 정규화 서명."""
    import json

    if not isinstance(plan_dict, dict):
        return ""

    def _canon(obj: object) -> object:
        if isinstance(obj, dict):
            return {str(k): _canon(obj[k]) for k in sorted(obj.keys(), key=str)}
        if isinstance(obj, list):
            return [_canon(x) for x in obj]
        if isinstance(obj, float):
            return round(obj, 6)
        return obj

    slim = {
        "operation": plan_dict.get("operation"),
        "steps": plan_dict.get("steps"),
        "group_by": plan_dict.get("group_by") or plan_dict.get("group_column"),
        "metrics": plan_dict.get("metrics"),
        "numerator": plan_dict.get("numerator"),
        "denominator": plan_dict.get("denominator"),
        "numeric_filters": plan_dict.get("numeric_filters"),
        "value_columns": plan_dict.get("value_columns"),
        "column": plan_dict.get("column"),
        "relation": plan_dict.get("relation"),
        "n": plan_dict.get("n") or plan_dict.get("limit"),
    }
    return json.dumps(_canon(slim), ensure_ascii=False, sort_keys=True)


# Human-readable labels for rejected analytical families (retry diversity).
OPERATION_FAMILY_LABELS: dict[str, str] = {
    "mean_based_filter": "mean-based filtering",
    "column_comparison_filter": "column-to-column comparison filtering",
    "entity_or_global_ranking": "aggregate-then-rank",
    "row_ranking": "row-level ranking",
    "within_group_ranking": "within-group ranking",
    "group_comparison": "group comparison",
    "ratio_derivation": "ratio / rate derivation",
    "rate_vs_mean": "rate-versus-mean filtering",
    "scalar_filter": "scalar threshold filtering",
    "other": "other analytical approach",
}


def _iter_plan_step_dicts(plan_dict: dict | None) -> list[dict]:
    if not isinstance(plan_dict, dict):
        return []
    out: list[dict] = []
    op_hl = plan_dict.get("operation")
    if isinstance(op_hl, str) and op_hl.strip():
        out.append({"op": op_hl.strip(), **{k: v for k, v in plan_dict.items() if k != "steps"}})
    steps = plan_dict.get("steps") or []
    if isinstance(steps, list):
        for s in steps:
            if isinstance(s, dict):
                out.append(s)
    return out


def _has_column_pair_numeric_filter(step: dict) -> bool:
    for nf in step.get("numeric_filters") or []:
        if not isinstance(nf, dict):
            continue
        left = nf.get("left_column")
        right = nf.get("right_column")
        if left and right:
            return True
    return bool(step.get("left_column") and step.get("right_column"))


def operation_family_signature(plan_dict: dict | None) -> str:
    """JSON 서명과 별도로, 같은 reasoning pattern(operation family)을 식별.

    예: filter_vs_mean → mean_based_filter
        filter_rows(left/right) → column_comparison_filter
    """
    steps = _iter_plan_step_dicts(plan_dict)
    if not steps:
        return "other"
    ops = [str(s.get("op") or s.get("operation") or "") for s in steps]
    has = set(ops)

    if any(_has_column_pair_numeric_filter(s) for s in steps):
        return "column_comparison_filter"
    if "filter_vs_mean" in has or str((plan_dict or {}).get("operation") or "") == "filter_vs_mean":
        # rate then vs-mean is a different family from bare mean filter
        if "ratio_of_aggregates" in has or "aggregate" in has:
            return "rate_vs_mean"
        return "mean_based_filter"
    if "top_per_group" in has or "top_n_per_group" in has:
        return "within_group_ranking"
    if "compare_groups" in has or str((plan_dict or {}).get("operation") or "") in {
        "group_comparison",
        "compare_groups",
    }:
        return "group_comparison"
    if "ratio_of_aggregates" in has or "derive_column" in has:
        if "sort" in has or "limit" in has or "filter_vs_mean" in has:
            return "ratio_derivation"
        return "ratio_derivation"
    if "aggregate" in has and ("sort" in has or "limit" in has):
        return "entity_or_global_ranking"
    if "sort" in has and "limit" in has and "aggregate" not in has:
        return "row_ranking"
    if "filter_rows" in has:
        # scalar / column_filters without left/right pair
        return "scalar_filter"
    return "other"


def operation_family_label(family: str | None) -> str:
    if not family:
        return OPERATION_FAMILY_LABELS["other"]
    return OPERATION_FAMILY_LABELS.get(family, family)


def repeated_operation_family_feedback(
    family: str | None,
    *,
    retry_mode: str | None = None,
) -> list[str]:
    """동일 invalid family 반복 시 Planner에게 다른 접근을 강제 (정답 op 미지정)."""
    label = operation_family_label(family)
    lines = [
        "The new plan repeats the same invalid analytical approach as the previous attempt.",
        f"Previous rejected family: {label}",
        "Reconsider the relationship between the available columns and use a "
        "materially different analytical approach.",
    ]
    if retry_mode == "regenerate":
        lines.append(
            "Avoid repeating the previously rejected analytical family unchanged. "
            "Explore another valid composition supported by the schema."
        )
    return lines
