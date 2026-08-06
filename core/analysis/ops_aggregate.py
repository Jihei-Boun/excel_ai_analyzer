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

    prefer_subtotals=True이면 소계 행을 우선하고, 없으면 detail 합산.
    소계와 detail 합이 모두 있으면 차이를 검증해 warnings에 남긴다.
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

    detail_sums, subtotal_vals, group_order = _collect_group_metrics(
        work,
        group_col=group_col,
        metric_specs=metric_specs,
    )

    warnings: list[str] = []
    rows: list[dict[str, object]] = []
    sources: dict[str, str] = {}

    ordered = list(group_order)
    if include_norm:
        # include에만 있고 데이터에 없는 그룹은 건너뛴다
        ordered = [g for g in ordered if normalize_text(g) in include_norm]
        for wanted in include_groups or []:
            key = normalize_text(wanted)
            if key and not any(normalize_text(g) == key for g in ordered):
                # detail/subtotal에 없던 라벨 — 원본 표기 유지 시도
                match = next(
                    (g for g in group_order if normalize_text(g) == key),
                    None,
                )
                if match and match not in ordered:
                    ordered.append(match)

    for group in ordered:
        detail = detail_sums.get(group)
        sub = subtotal_vals.get(group)
        row: dict[str, object] = {group_col: group}
        source = "detail_sum"

        if prefer_subtotals and sub is not None:
            for name, _fn in metric_specs:
                row[name] = sub.get(name)
            source = "subtotal"
            if detail is not None:
                for name, _fn in metric_specs:
                    s_val = sub.get(name)
                    d_val = detail.get(name)
                    if s_val is None or d_val is None:
                        continue
                    if abs(float(s_val) - float(d_val)) > max(1.0, abs(float(d_val)) * 0.01):
                        warnings.append(
                            f"{group}: 소계 `{name}`={s_val:,.0f} vs "
                            f"상세합={d_val:,.0f} 차이"
                        )
        elif detail is not None:
            for name, _fn in metric_specs:
                row[name] = detail.get(name)
            source = "detail_sum"
        elif sub is not None:
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


def _normalize_metrics(
    metrics: list[dict[str, str]] | list[str],
    columns: pd.Index,
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in metrics or []:
        if isinstance(item, str):
            col, fn = item, "sum"
        elif isinstance(item, dict):
            col = str(item.get("column") or item.get("name") or "")
            fn = str(item.get("fn") or item.get("agg") or "sum").lower()
        else:
            continue
        if col not in columns:
            continue
        if fn not in AGGREGATE_FNS:
            fn = "sum"
        out.append((col, fn))
    return out


def _collect_group_metrics(
    df: pd.DataFrame,
    *,
    group_col: str,
    metric_specs: list[tuple[str, str]],
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]], list[str]]:
    detail_sums: dict[str, dict[str, float]] = {}
    subtotal_vals: dict[str, dict[str, float]] = {}
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
            bucket = detail_sums.setdefault(raw_label, {m: 0.0 for m, _ in metric_specs})
            for name, fn in metric_specs:
                val = pd.to_numeric(row.get(name), errors="coerce")
                if pd.isna(val):
                    continue
                if fn == "sum":
                    bucket[name] = float(bucket.get(name, 0.0)) + float(val)
                elif fn == "count":
                    bucket[name] = float(bucket.get(name, 0.0)) + 1.0
                elif fn == "mean":
                    # 임시: sum 누적 후 나중에 count로 나누지 않음 — sum만 기본 지원 강화
                    bucket[name] = float(bucket.get(name, 0.0)) + float(val)
                elif fn == "min":
                    prev = bucket.get(name)
                    bucket[name] = float(val) if prev is None else min(float(prev), float(val))
                elif fn == "max":
                    prev = bucket.get(name)
                    bucket[name] = float(val) if prev is None else max(float(prev), float(val))
            continue

        if rtype in {"subtotal", "blank"} or is_summary_label:
            # blank+금액(소계) 또는 명시적 subtotal
            has_amount = any(
                pd.notna(pd.to_numeric(row.get(name), errors="coerce"))
                for name, _ in metric_specs
            )
            if rtype == "blank" and not is_summary_label and not has_amount:
                continue
            if rtype not in {"subtotal", "blank"} and not is_summary_label:
                continue
            # 소계는 직전 그룹에 귀속
            target = last_group
            if target is None:
                continue
            vals = {}
            for name, _fn in metric_specs:
                val = pd.to_numeric(row.get(name), errors="coerce")
                vals[name] = None if pd.isna(val) else float(val)
            # 이미 소계가 있으면 첫 소계 유지
            if target not in subtotal_vals:
                subtotal_vals[target] = vals
            continue

    return detail_sums, subtotal_vals, group_order
