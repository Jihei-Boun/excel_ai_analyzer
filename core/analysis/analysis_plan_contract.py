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

#### Global top-N / largest / smallest (single overall ranking)
Examples of shape (not phrases): "top N items by metric", "the largest value", "highest N".
If the metric ALREADY exists on detail rows:
  annotate → filter detail → sort(by=metric) → limit(n)
Do NOT aggregate first when the row already has the metric and the user wants the top rows.
Do NOT use top_per_group for a single global ranking.
Do NOT use filter_vs_mean to find a max/min.
Do NOT use find_items with op=max/min — use sort → limit.

If the metric must be totaled per category first (category totals ranking):
  aggregate(group_by=category, metrics=[{column, fn}]) → sort → limit

If ranking a RATE/RATIO:
  aggregate → ratio_of_aggregates(name=rate) → sort(by=rate) → limit
Never rank a rate with top_per_group alone. Never omit ratio_of_aggregates.

#### Group-wise top-N (ranking inside each group)
Only when EACH group needs its own top-N members:
  (optional aggregate if totals needed) → top_per_group(group_column, value_column, n)
Examples of shape: "top N products in EACH region", "top 2 people in EACH department".
The presence of a category word alone does NOT imply group-wise ranking.
If the request is one overall ranking, use sort → limit.

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

### Above / below mean
Row-level metric vs its mean: filter_vs_mean(column, above|below).
Group totals vs mean of totals: aggregate first → filter_vs_mean on the aggregated metric.
Ratio vs its mean: rate_vs_mean.
Do NOT use filter_vs_mean to compare two different columns (use column-vs-column filters).

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
required: group_column, groups, metrics that already exist in the plan
If comparing a rate, ratio_of_aggregates must precede compare_groups.

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
)


def composition_category_from_issues(codes: list[str]) -> str | None:
    """validation issue code → composition failure category."""
    joined = " ".join(codes)
    if "misused_top_per_group" in joined or "conflicting_ranking" in joined:
        return "misused_top_per_group"
    if "missing_ratio" in joined or "ratio_required" in joined:
        return "missing_ratio"
    if "missing_sort_column" in joined or "missing_metric_before" in joined:
        return "missing_metric_before_sort"
    if "compare_metric" in joined or "compare_before_metric" in joined:
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
