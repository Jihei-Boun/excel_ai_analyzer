"""집계·순위·평균 대비 필터 연산."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.pai.pandasai_frame import is_total_label
from core.schema.row_classify import (
    META_COLUMNS_SET,
    ROW_TYPE_COL,
    classify_rows,
    infer_dimension_columns,
)
from core.summary.summary_utils import cell_text
from core.io.text_normalize import normalize_text

from core.analysis.ops_filters import AGGREGATE_FNS, ensure_row_types

def top_per_group(
    df: pd.DataFrame,
    *,
    group_column: str,
    value_column: str,
    n: int = 1,
    ascending: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """그룹마다 ``value_column`` 기준 상위/하위 n행을 고른다."""
    if group_column not in df.columns:
        raise ValueError(f"top_per_group 그룹 컬럼 없음: {group_column}")
    if value_column not in df.columns:
        raise ValueError(f"top_per_group 값 컬럼 없음: {value_column}")
    n = max(1, min(50, int(n)))
    work = df.copy()
    work["_top_val"] = pd.to_numeric(work[value_column], errors="coerce")
    # 결측 값은 비교에서 제외
    work = work.loc[work["_top_val"].notna()].copy()
    if work.empty:
        return work.drop(columns=["_top_val"], errors="ignore"), {
            "group_column": group_column,
            "value_column": value_column,
            "n": n,
            "groups": 0,
            "kept": 0,
        }

    parts: list[pd.DataFrame] = []
    for _, chunk in work.groupby(group_column, sort=False, dropna=False):
        ordered = chunk.sort_values(
            "_top_val",
            ascending=ascending,
            kind="mergesort",
        )
        parts.append(ordered.head(n))
    out = pd.concat(parts, axis=0).drop(columns=["_top_val"])
    meta = {
        "group_column": group_column,
        "value_column": value_column,
        "n": n,
        "ascending": ascending,
        "groups": int(work[group_column].nunique(dropna=False)),
        "kept": int(len(out)),
    }
    return out.reset_index(drop=True), meta


def filter_vs_mean(
    df: pd.DataFrame,
    *,
    column: str,
    relation: str = "below",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """``column``의 산술평균과 비교해 행을 남긴다.

    relation: below | above | below_or_equal | above_or_equal
    결측은 비교·평균에서 제외한다.
    """
    if column not in df.columns:
        raise ValueError(f"filter_vs_mean 컬럼 없음: {column}")
    vals = pd.to_numeric(df[column], errors="coerce")
    valid = vals.dropna()
    if valid.empty:
        return df.iloc[0:0].copy(), {
            "column": column,
            "mean": None,
            "n_valid": 0,
            "relation": relation,
            "kept": 0,
        }
    mean = float(valid.mean())
    rel = str(relation or "below").lower().strip()
    if rel in {"below", "lt", "lower", "미만", "낮은"}:
        mask = vals < mean
        rel = "below"
    elif rel in {"above", "gt", "higher", "초과", "높은"}:
        mask = vals > mean
        rel = "above"
    elif rel in {"below_or_equal", "lte", "이하"}:
        mask = vals <= mean
        rel = "below_or_equal"
    else:
        mask = vals >= mean
        rel = "above_or_equal"
    mask = mask.fillna(False)
    out = df.loc[mask].copy()
    meta = {
        "column": column,
        "mean": mean,
        "n_valid": int(len(valid)),
        "relation": rel,
        "kept": int(mask.sum()),
    }
    return out, meta


def aggregate_groups(
    df: pd.DataFrame,
    *,
    group_by: list[str],
    metrics: list[dict[str, str]],
    prefer_subtotals: bool = True,
    include_groups: list[str] | None = None,
    dimension_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """그룹별 지표 표를 만든다.

    ``fn`` 지원: sum, mean/avg, median, min, max, count.
    prefer_subtotals=True는 **sum** 지표에만 소계 행을 우선한다.
    mean/count/min/max/median은 detail 행에서 직접 계산한다 (소계는 합계 전용).
    """
    if not group_by:
        raise ValueError("aggregate에 group_by가 필요합니다.")
    group_col = group_by[0]
    if group_col not in df.columns:
        raise ValueError(f"group_by 컬럼 없음: {group_col}")

    work = ensure_row_types(df, dimension_columns=dimension_columns)
    metric_specs = _normalize_metrics(metrics, work.columns)
    if not metric_specs:
        raise ValueError("aggregate에 유효한 metrics가 없습니다.")

    include_norm = (
        {normalize_text(v) for v in include_groups if str(v).strip()}
        if include_groups
        else None
    )

    detail_vals, subtotal_vals, group_order = _collect_group_metrics(
        work,
        group_col=group_col,
        metric_specs=metric_specs,
    )

    warnings: list[str] = []
    rows: list[dict[str, object]] = []
    sources: dict[str, str] = {}

    ordered = list(group_order)
    if include_norm:
        ordered = [g for g in ordered if normalize_text(g) in include_norm]
        for wanted in include_groups or []:
            key = normalize_text(wanted)
            if key and not any(normalize_text(g) == key for g in ordered):
                match = next(
                    (g for g in group_order if normalize_text(g) == key),
                    None,
                )
                if match and match not in ordered:
                    ordered.append(match)

    sum_only = all(fn in {"sum"} for _, fn in metric_specs)
    use_subtotals = bool(prefer_subtotals) and sum_only
    if prefer_subtotals and not sum_only:
        warnings.append(
            "prefer_subtotals ignored for non-sum aggregations "
            f"({', '.join(sorted({fn for _, fn in metric_specs}))}); "
            "computed from detail rows."
        )

    for group in ordered:
        detail = detail_vals.get(group)
        sub = subtotal_vals.get(group)
        row: dict[str, object] = {group_col: group}
        source = "detail"

        if use_subtotals and sub is not None:
            for name, _fn in metric_specs:
                row[name] = sub.get(name)
            source = "subtotal"
            if detail is not None:
                for name, fn in metric_specs:
                    if fn != "sum":
                        continue
                    s_val = sub.get(name)
                    d_val = _reduce_values(detail.get(name) or [], "sum")
                    if s_val is None or d_val is None:
                        continue
                    if abs(float(s_val) - float(d_val)) > max(1.0, abs(float(d_val)) * 0.01):
                        warnings.append(
                            f"{group}: 소계 `{name}`={s_val:,.0f} vs "
                            f"상세합={d_val:,.0f} 차이"
                        )
        elif detail is not None:
            for name, fn in metric_specs:
                row[name] = _reduce_values(detail.get(name) or [], fn)
            fns = {fn for _, fn in metric_specs}
            if fns == {"sum"}:
                source = "detail_sum"
            elif len(fns) == 1:
                source = f"detail_{next(iter(fns))}"
            else:
                source = "detail"
        elif sub is not None and sum_only:
            for name, _fn in metric_specs:
                row[name] = sub.get(name)
            source = "subtotal"
        else:
            continue

        rows.append(row)
        sources[group] = source

    result = pd.DataFrame(rows)
    meta = {
        "aggregate_sources": sources,
        "aggregate_warnings": warnings,
        "prefer_subtotals": prefer_subtotals,
        "aggregations": {name: fn for name, fn in metric_specs},
    }
    return result, meta


def ratio_of_columns(
    df: pd.DataFrame,
    *,
    name: str,
    numerator: str,
    denominator: str,
) -> pd.DataFrame:
    """행별 합계 간 비율. 분모 0/결측은 NA."""
    if numerator not in df.columns or denominator not in df.columns:
        raise ValueError(f"ratio 컬럼 없음: {numerator}, {denominator}")
    work = df.copy()
    num = pd.to_numeric(work[numerator], errors="coerce")
    den = pd.to_numeric(work[denominator], errors="coerce").replace(0, pd.NA)
    work[name] = num / den
    return work


def _canonical_agg_fn(fn: str) -> str:
    raw = str(fn or "sum").lower().strip()
    aliases = {
        "avg": "mean",
        "average": "mean",
        "med": "median",
        "n": "count",
        "cnt": "count",
        "total": "sum",
    }
    return aliases.get(raw, raw)


def _normalize_metrics(
    metrics: list[dict[str, str]] | list[str],
    columns: pd.Index,
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    unsupported: list[str] = []
    for item in metrics or []:
        if isinstance(item, str):
            col, fn = item, "sum"
        elif isinstance(item, dict):
            col = str(item.get("column") or item.get("name") or "")
            fn = _canonical_agg_fn(str(item.get("fn") or item.get("agg") or "sum"))
        else:
            continue
        if col not in columns:
            continue
        if fn not in AGGREGATE_FNS:
            unsupported.append(fn)
            continue
        out.append((col, fn))
    if unsupported:
        raise ValueError(
            "unsupported aggregation fn: "
            + ", ".join(sorted(set(unsupported)))
            + f"; allowed={sorted(AGGREGATE_FNS)}"
        )
    return out


def _reduce_values(values: list[float], fn: str) -> float | None:
    if fn == "count":
        return float(len(values))
    if not values:
        return None
    series = pd.Series(values, dtype="float64")
    if fn == "sum":
        return float(series.sum())
    if fn in {"mean", "avg"}:
        return float(series.mean())
    if fn == "median":
        return float(series.median())
    if fn == "min":
        return float(series.min())
    if fn == "max":
        return float(series.max())
    raise ValueError(f"unsupported aggregation fn: {fn}")


def _collect_group_metrics(
    df: pd.DataFrame,
    *,
    group_col: str,
    metric_specs: list[tuple[str, str]],
) -> tuple[
    dict[str, dict[str, list[float]]],
    dict[str, dict[str, float | None]],
    list[str],
]:
    """detail은 값 리스트, subtotal은 표시된 합계값(dict)."""
    detail_vals: dict[str, dict[str, list[float]]] = {}
    subtotal_vals: dict[str, dict[str, float | None]] = {}
    group_order: list[str] = []
    seen: set[str] = set()
    last_group: str | None = None

    for _, row in df.iterrows():
        rtype = str(row.get(ROW_TYPE_COL) or "")
        raw_label = cell_text(row.get(group_col))
        is_summary_label = bool(raw_label) and is_total_label(raw_label)

        if rtype == "detail" and raw_label and not is_summary_label:
            last_group = raw_label
            if raw_label not in seen:
                seen.add(raw_label)
                group_order.append(raw_label)
            bucket = detail_vals.setdefault(
                raw_label, {m: [] for m, _ in metric_specs}
            )
            for name, fn in metric_specs:
                if fn == "count":
                    # count: non-null cells (numeric or any present)
                    cell = row.get(name)
                    if cell_text(cell) or pd.notna(pd.to_numeric(cell, errors="coerce")):
                        bucket[name].append(1.0)
                    continue
                val = pd.to_numeric(row.get(name), errors="coerce")
                if pd.isna(val):
                    continue
                bucket[name].append(float(val))
            continue

        if rtype in {"subtotal", "blank"} or is_summary_label:
            has_amount = any(
                pd.notna(pd.to_numeric(row.get(name), errors="coerce"))
                for name, _ in metric_specs
            )
            if rtype == "blank" and not is_summary_label and not has_amount:
                continue
            if rtype not in {"subtotal", "blank"} and not is_summary_label:
                continue
            target = last_group
            if target is None:
                continue
            vals: dict[str, float | None] = {}
            for name, _fn in metric_specs:
                val = pd.to_numeric(row.get(name), errors="coerce")
                vals[name] = None if pd.isna(val) else float(val)
            if target not in subtotal_vals:
                subtotal_vals[target] = vals
            continue

    return detail_vals, subtotal_vals, group_order
