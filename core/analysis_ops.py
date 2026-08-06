"""범용 분석 연산 — aggregate / ratio / compare / distribution / correlation."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.pandasai_frame import is_total_label
from core.row_classify import (
    META_COLUMNS_SET,
    ROW_TYPE_COL,
    classify_rows,
    infer_dimension_columns,
)
from core.summary_utils import cell_text
from core.text_normalize import normalize_text

AGGREGATE_FNS = frozenset({"sum", "mean", "min", "max", "count"})
CORR_ZERO_EPS = 1e-12


def ensure_row_types(
    df: pd.DataFrame,
    *,
    dimension_columns: list[str] | None = None,
    footer_labels: list[str] | tuple[str, ...] | None = None,
) -> pd.DataFrame:
    if ROW_TYPE_COL in df.columns:
        return df
    dims = dimension_columns or infer_dimension_columns(
        df.drop(columns=[c for c in META_COLUMNS_SET if c in df.columns], errors="ignore")
    )
    base = df.drop(columns=[c for c in META_COLUMNS_SET if c in df.columns], errors="ignore")
    return classify_rows(
        base,
        dimension_columns=dims,
        footer_labels=footer_labels,
    )


def apply_column_filters(
    df: pd.DataFrame,
    column_filters: list[dict[str, Any]] | None,
) -> pd.DataFrame:
    """``[{column, values}]`` 값 포함 필터. 정규화 문자열 비교."""
    if not column_filters:
        return df
    work = df
    for spec in column_filters:
        if not isinstance(spec, dict):
            continue
        column = str(spec.get("column") or "")
        values = spec.get("values") or []
        if isinstance(values, str):
            values = [values]
        values = [str(v) for v in values if str(v).strip()]
        if not column or column not in work.columns or not values:
            continue
        targets = {normalize_text(v) for v in values}
        mask = work[column].map(lambda v: normalize_text(cell_text(v)) in targets)
        work = work.loc[mask]
    return work


NUMERIC_FILTER_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte"})


def apply_numeric_filters(
    df: pd.DataFrame,
    numeric_filters: list[dict[str, Any]] | None,
) -> pd.DataFrame:
    """``[{column, op, value}]`` 수치 비교 필터. op: eq/ne/gt/gte/lt/lte."""
    if not numeric_filters:
        return df
    work = df
    for spec in numeric_filters:
        if not isinstance(spec, dict):
            continue
        column = str(spec.get("column") or "")
        op = str(spec.get("op") or spec.get("operator") or "").lower().strip()
        if column not in work.columns or op not in NUMERIC_FILTER_OPS:
            continue
        try:
            threshold = float(spec.get("value"))
        except (TypeError, ValueError):
            continue
        series = pd.to_numeric(work[column], errors="coerce")
        if op == "eq":
            mask = series.fillna(threshold + 1) == threshold
        elif op == "ne":
            mask = series.fillna(threshold) != threshold
        elif op == "gt":
            mask = series.fillna(threshold) > threshold
        elif op == "gte":
            mask = series.fillna(threshold - 1) >= threshold
        elif op == "lt":
            mask = series.fillna(threshold) < threshold
        else:  # lte
            mask = series.fillna(threshold + 1) <= threshold
        work = work.loc[mask]
    return work


def project_readable_columns(
    df: pd.DataFrame,
    *,
    keep_columns: list[str] | None = None,
    preferred_labels: tuple[str, ...] | None = None,
    profile_name: str | None = None,
    use_budget_profile: bool = False,
) -> pd.DataFrame:
    """식별·조건 확인에 필요한 열만 남긴다. keep가 없으면 라벨 열만.

    preferred_labels 기본값은 활성 프로필에서 가져온다 (일반 모드는 도메인 비목 가정 없음).
    """
    if df is None or df.empty:
        return df
    if preferred_labels is None:
        from core.profile_loader import preferred_labels_for

        preferred_labels = preferred_labels_for(
            profile_name=profile_name, use_budget_profile=use_budget_profile,
        )
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(col: str) -> None:
        if col in df.columns and col not in seen:
            ordered.append(col)
            seen.add(col)

    for col in preferred_labels:
        _add(col)
    for col in keep_columns or []:
        _add(str(col))
    if not ordered:
        return df
    return df.loc[:, ordered].copy()


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


def compare_groups(
    df: pd.DataFrame,
    *,
    group_column: str,
    metrics: list[str],
    groups: list[str] | None = None,
    rate_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """그룹 비교 표를 정리하고 차이·%p 메타를 반환한다."""
    if group_column not in df.columns:
        raise ValueError(f"compare_groups 컬럼 없음: {group_column}")

    work = df.copy()
    if groups:
        targets = {normalize_text(v) for v in groups}
        work = work.loc[
            work[group_column].map(lambda v: normalize_text(cell_text(v)) in targets)
        ].copy()
        # 요청 순서 유지
        order = {normalize_text(v): i for i, v in enumerate(groups)}
        work["_cmp_order"] = work[group_column].map(
            lambda v: order.get(normalize_text(cell_text(v)), 999)
        )
        work = work.sort_values("_cmp_order", kind="mergesort").drop(columns=["_cmp_order"])

    metric_cols = [m for m in metrics if m in work.columns]
    keep = [group_column] + metric_cols
    out = work[keep].reset_index(drop=True)

    meta: dict[str, Any] = {"comparison": []}
    rate_set = {normalize_text(c) for c in (rate_columns or [])}
    rate_set |= {
        normalize_text(c)
        for c in metric_cols
        if any(tok in normalize_text(c) for tok in ("률", "비율", "rate", "ratio", "집행률"))
    }

    for col in metric_cols:
        vals = []
        for _, row in out.iterrows():
            vals.append(
                (
                    cell_text(row[group_column]),
                    pd.to_numeric(row[col], errors="coerce"),
                )
            )
        valid = [(g, float(v)) for g, v in vals if pd.notna(v)]
        if len(valid) < 2:
            continue
        (g_hi, v_hi), (g_lo, v_lo) = max(valid, key=lambda x: x[1]), min(
            valid, key=lambda x: x[1]
        )
        diff = v_hi - v_lo
        entry: dict[str, Any] = {
            "metric": col,
            "higher_group": g_hi,
            "lower_group": g_lo,
            "higher_value": v_hi,
            "lower_value": v_lo,
            "diff": diff,
        }
        if normalize_text(col) in rate_set or (
            abs(v_hi) <= 1.5 and abs(v_lo) <= 1.5
        ):
            entry["diff_pp"] = diff * 100.0
        meta["comparison"].append(entry)

    # 구조화 payload (해석 LLM용)
    payload_rows = []
    for _, row in out.iterrows():
        item = {group_column: cell_text(row[group_column])}
        for col in metric_cols:
            val = pd.to_numeric(row[col], errors="coerce")
            item[col] = None if pd.isna(val) else float(val)
        payload_rows.append(item)
    meta["structured"] = payload_rows
    return out, meta


def distribution_summary(
    df: pd.DataFrame,
    *,
    group_column: str | None,
    item_column: str | None,
    budget_column: str,
    executed_column: str,
    group_value: str | None = None,
    zero_threshold: float = 0.0,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """그룹 내 집행률 분포 요약 (0%·고/저집행 등)."""
    work = ensure_row_types(df)
    if ROW_TYPE_COL in work.columns:
        work = work.loc[work[ROW_TYPE_COL].astype(str) == "detail"].copy()

    if group_column and group_value and group_column in work.columns:
        target = normalize_text(group_value)
        work = work.loc[
            work[group_column].map(lambda v: normalize_text(cell_text(v)) == target)
        ].copy()

    if budget_column not in work.columns or executed_column not in work.columns:
        raise ValueError("distribution_summary에 예산/집행 컬럼이 필요합니다.")

    budget = pd.to_numeric(work[budget_column], errors="coerce")
    executed = pd.to_numeric(work[executed_column], errors="coerce")
    rate = executed / budget.replace(0, pd.NA)
    work = work.copy()
    work["_budget"] = budget
    work["_executed"] = executed
    work["_rate"] = rate

    item_col = item_column
    if not item_col or item_col not in work.columns:
        candidates = [
            c
            for c in work.columns
            if c not in META_COLUMNS_SET
            and c != group_column
            and not pd.api.types.is_numeric_dtype(work[c])
        ]
        item_col = candidates[0] if candidates else None

    labels = (
        work[item_col].map(cell_text)
        if item_col
        else pd.Series([str(i) for i in range(len(work))], index=work.index)
    )

    zero_mask = work["_rate"].fillna(-1) <= zero_threshold
    zero_items = [
        {"항목": labels.loc[i], "예산": float(work.loc[i, "_budget"] or 0)}
        for i in work.index[zero_mask]
        if cell_text(labels.loc[i])
    ]
    nonzero = work.loc[~zero_mask & work["_rate"].notna()].copy()
    high = []
    low_big = []
    if not nonzero.empty:
        top = nonzero.sort_values("_rate", ascending=False).head(3)
        for i, row in top.iterrows():
            high.append(
                {
                    "항목": cell_text(labels.loc[i]),
                    "집행률": float(row["_rate"]),
                    "예산": float(row["_budget"] or 0),
                }
            )
        # 예산 상위 중 저집행
        big = nonzero.sort_values("_budget", ascending=False).head(5)
        low_big = [
            {
                "항목": cell_text(labels.loc[i]),
                "집행률": float(row["_rate"]),
                "예산": float(row["_budget"] or 0),
            }
            for i, row in big.iterrows()
            if float(row["_rate"]) < 0.3
        ]

    rates = work["_rate"].dropna()
    meta = {
        "group": group_value,
        "item_count": int(len(work)),
        "zero_rate_count": len(zero_items),
        "zero_rate_items": zero_items[:20],
        "zero_budget_sum": float(work.loc[zero_mask, "_budget"].fillna(0).sum()),
        "max_rate": float(rates.max()) if not rates.empty else None,
        "min_rate": float(rates.min()) if not rates.empty else None,
        "high_rate_items": high,
        "low_rate_large_budget_items": low_big,
    }

    summary_rows = [
        {
            "지표": "항목수",
            "값": meta["item_count"],
        },
        {
            "지표": "0%미집행수",
            "값": meta["zero_rate_count"],
        },
        {
            "지표": "미집행예산합",
            "값": meta["zero_budget_sum"],
        },
        {
            "지표": "최대집행률",
            "값": meta["max_rate"],
        },
        {
            "지표": "최소집행률",
            "값": meta["min_rate"],
        },
    ]
    return pd.DataFrame(summary_rows), meta


def correlation_of_columns(
    df: pd.DataFrame,
    *,
    x_column: str,
    y_column: str,
    label_column: str | None = None,
    methods: list[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """세부 행 기준 두 수치 열의 상관계수·분포 요약을 만든다.

    비율(집행률)이나 그룹 집계가 아니라, 행 단위 Pearson/Spearman 상관이다.
    분모 0 때문에 비율이 0이 되는 것과 무상관을 혼동하지 않는다.
    """
    if x_column not in df.columns or y_column not in df.columns:
        raise ValueError(f"correlation 컬럼 없음: {x_column}, {y_column}")

    wanted = {str(m).lower() for m in (methods or ["pearson", "spearman"])}
    work = df.copy()
    x = pd.to_numeric(work[x_column], errors="coerce")
    y = pd.to_numeric(work[y_column], errors="coerce")
    valid = x.notna() & y.notna()
    xv = x.loc[valid]
    yv = y.loc[valid]
    n = int(len(xv))

    pearson_r: float | None = None
    spearman_rho: float | None = None
    r_squared: float | None = None
    if n >= 2:
        if "pearson" in wanted:
            pearson_r = _safe_corr(xv, yv)
            if pearson_r is not None:
                r_squared = float(pearson_r**2)
        if "spearman" in wanted:
            # scipy 없이: 순위 변환 후 Pearson (= Spearman ρ)
            spearman_rho = _safe_corr(xv.rank(), yv.rank())

    x_pos = xv > CORR_ZERO_EPS
    y_pos = yv > CORR_ZERO_EPS
    x_zero = xv.abs() <= CORR_ZERO_EPS
    y_zero = yv.abs() <= CORR_ZERO_EPS
    both_pos_mask = x_pos & y_pos
    x_only_mask = x_pos & y_zero
    y_only_mask = y_pos & x_zero
    both_zero_mask = x_zero & y_zero

    label_col = label_column if label_column and label_column in work.columns else None
    if label_col is None:
        candidates = [
            c
            for c in work.columns
            if c not in META_COLUMNS_SET
            and c not in {x_column, y_column}
            and not pd.api.types.is_numeric_dtype(work[c])
        ]
        label_col = candidates[0] if candidates else None

    both_pos_rows: list[dict[str, Any]] = []
    if both_pos_mask.any():
        idx = xv.index[both_pos_mask]
        for i in idx:
            row: dict[str, Any] = {
                x_column: float(xv.loc[i]),
                y_column: float(yv.loc[i]),
            }
            if label_col is not None:
                row[label_col] = cell_text(work.loc[i, label_col])
            both_pos_rows.append(row)

    # 양수 교집합만으로의 참고 상관 (표본이 작으면 해석 시 경고)
    both_pos_r: float | None = None
    both_pos_n = int(both_pos_mask.sum())
    if both_pos_n >= 2 and "pearson" in wanted:
        both_pos_r = _safe_corr(xv.loc[both_pos_mask], yv.loc[both_pos_mask])

    summary_rows: list[dict[str, Any]] = [
        {"지표": "Pearson_r", "값": pearson_r},
        {"지표": "Spearman_rho", "값": spearman_rho},
        {"지표": "R2", "값": r_squared},
        {"지표": "표본수", "값": n},
        {"지표": f"{x_column}_합계", "값": float(xv.sum()) if n else 0.0},
        {"지표": f"{y_column}_합계", "값": float(yv.sum()) if n else 0.0},
        {"지표": f"{x_column}_양수행", "값": int(x_pos.sum())},
        {"지표": f"{y_column}_양수행", "값": int(y_pos.sum())},
        {"지표": "둘다_양수", "값": both_pos_n},
        {"지표": f"{x_column}만_양수", "값": int(x_only_mask.sum())},
        {"지표": f"{y_column}만_양수", "값": int(y_only_mask.sum())},
        {"지표": "둘다_0", "값": int(both_zero_mask.sum())},
    ]
    if both_pos_r is not None:
        summary_rows.append({"지표": "둘다_양수_Pearson_r", "값": both_pos_r})

    meta: dict[str, Any] = {
        "x_column": x_column,
        "y_column": y_column,
        "label_column": label_col,
        "n": n,
        "pearson_r": pearson_r,
        "spearman_rho": spearman_rho,
        "r_squared": r_squared,
        "x_sum": float(xv.sum()) if n else 0.0,
        "y_sum": float(yv.sum()) if n else 0.0,
        "x_positive_count": int(x_pos.sum()),
        "y_positive_count": int(y_pos.sum()),
        "both_positive_count": both_pos_n,
        "x_only_positive_count": int(x_only_mask.sum()),
        "y_only_positive_count": int(y_only_mask.sum()),
        "both_zero_count": int(both_zero_mask.sum()),
        "both_positive_rows": both_pos_rows[:20],
        "both_positive_pearson_r": both_pos_r,
        "strength": _correlation_strength(pearson_r),
    }
    warnings: list[str] = []
    if n < 3:
        warnings.append(f"상관 표본이 {n}행으로 매우 작습니다.")
    if both_pos_n < 5 and both_pos_n > 0:
        warnings.append(
            f"두 열 모두 양수인 행이 {both_pos_n}개뿐입니다. "
            "교집합만의 강한 상관을 전체 결론으로 쓰면 안 됩니다."
        )
    if both_pos_n == 0:
        warnings.append("두 열 모두 양수인 행이 없어 동시 발생 패턴이 거의 없습니다.")
    meta["warnings"] = warnings
    return pd.DataFrame(summary_rows), meta


def _safe_corr(left: pd.Series, right: pd.Series) -> float | None:
    """Pearson 상관. 분산 0·결측이면 None (scipy 불필요)."""
    if len(left) < 2 or len(right) < 2:
        return None
    value = left.corr(right, method="pearson")
    if value is None or pd.isna(value):
        return None
    return float(value)


def _correlation_strength(r: float | None) -> str:
    if r is None or pd.isna(r):
        return "계산불가"
    ar = abs(float(r))
    if ar < 0.1:
        return "무상관~매우약함"
    if ar < 0.3:
        return "약함"
    if ar < 0.5:
        return "보통"
    if ar < 0.7:
        return "뚜렷"
    return "강함"


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
