"""그룹 비교·분포·상관 연산."""

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

from core.analysis.ops_filters import ensure_row_types

CORR_ZERO_EPS = 1e-12

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
    from core.profile_loader import display_labels_for

    rate_set = {normalize_text(c) for c in (rate_columns or [])}
    rate_tokens = ["률", "비율", "rate", "ratio"]
    rate_label = display_labels_for().get("rate") or ""
    if rate_label:
        rate_tokens.append(normalize_text(rate_label))
    rate_set |= {
        normalize_text(c)
        for c in metric_cols
        if any(tok in normalize_text(c) for tok in rate_tokens)
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
    denominator_column: str | None = None,
    numerator_column: str | None = None,
    budget_column: str | None = None,
    executed_column: str | None = None,
    group_value: str | None = None,
    zero_threshold: float = 0.0,
    profile_name: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """그룹 내 비율(분자/분모) 분포 요약.

    ``denominator_column`` / ``numerator_column`` 이 정식 이름이다,
    하위 호환으로 ``budget_column`` / ``executed_column`` 별칭을 받는다.
    """
    from core.profile_loader import display_labels_for

    labels = display_labels_for(profile_name=profile_name)
    item_key = labels.get("item", "항목")
    den_key = labels.get("denominator", "분모")
    rate_key = labels.get("rate", "비율")

    den_col = str(denominator_column or budget_column or "").strip()
    num_col = str(numerator_column or executed_column or "").strip()
    work = ensure_row_types(df)
    if ROW_TYPE_COL in work.columns:
        work = work.loc[work[ROW_TYPE_COL].astype(str) == "detail"].copy()

    if group_column and group_value and group_column in work.columns:
        target = normalize_text(group_value)
        work = work.loc[
            work[group_column].map(lambda v: normalize_text(cell_text(v)) == target)
        ].copy()

    if den_col not in work.columns or num_col not in work.columns:
        raise ValueError(
            "distribution_summary에 denominator/numerator(또는 budget/executed) 컬럼이 필요합니다."
        )

    budget = pd.to_numeric(work[den_col], errors="coerce")
    executed = pd.to_numeric(work[num_col], errors="coerce")
    rate = executed / budget.replace(0, pd.NA)
    work = work.copy()
    work["_budget"] = budget
    work["_executed"] = executed
    work["_rate"] = rate
    work["_denominator"] = budget
    work["_numerator"] = executed

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

    row_labels = (
        work[item_col].map(cell_text)
        if item_col
        else pd.Series([str(i) for i in range(len(work))], index=work.index)
    )

    zero_mask = work["_rate"].fillna(-1) <= zero_threshold
    zero_items = [
        {item_key: row_labels.loc[i], den_key: float(work.loc[i, "_budget"] or 0)}
        for i in work.index[zero_mask]
        if cell_text(row_labels.loc[i])
    ]
    nonzero = work.loc[~zero_mask & work["_rate"].notna()].copy()
    high = []
    low_big = []
    if not nonzero.empty:
        top = nonzero.sort_values("_rate", ascending=False).head(3)
        for i, row in top.iterrows():
            high.append(
                {
                    item_key: cell_text(row_labels.loc[i]),
                    rate_key: float(row["_rate"]),
                    den_key: float(row["_budget"] or 0),
                }
            )
        # 분모 상위 중 저비율
        big = nonzero.sort_values("_budget", ascending=False).head(5)
        low_big = [
            {
                item_key: cell_text(row_labels.loc[i]),
                rate_key: float(row["_rate"]),
                den_key: float(row["_budget"] or 0),
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
            "지표": labels.get("item_count", "항목수"),
            "값": meta["item_count"],
        },
        {
            "지표": labels.get("zero_rate_count", "0%비율수"),
            "값": meta["zero_rate_count"],
        },
        {
            "지표": labels.get("zero_denominator_sum", "0%분모합"),
            "값": meta["zero_budget_sum"],
        },
        {
            "지표": labels.get("max_rate", "최대비율"),
            "값": meta["max_rate"],
        },
        {
            "지표": labels.get("min_rate", "최소비율"),
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


