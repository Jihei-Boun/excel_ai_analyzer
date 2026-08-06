"""결정론적 범용 실행 엔진 — 허용된 연산만 수행."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.integrate.plan_types import DerivedRowSpec, ExecutionPlan
from core.schema.row_classify import (
    ROW_CONF_COL,
    ROW_REASONS_COL,
    ROW_TYPE_COL,
    classify_rows as classify_row_roles,
)
from core.io.text_normalize import normalize_text


def execute_plan(
    plan: ExecutionPlan,
    dataframes: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    """실행 계획을 수행하고 sheets/integrated/detail 메타를 반환한다."""
    if plan.operation != "aggregate_merge":
        raise ValueError(
            f"현재 엔진은 aggregate_merge만 지원합니다: {plan.operation!r}"
        )
    return _execute_aggregate_merge(plan, dataframes)


def _execute_aggregate_merge(
    plan: ExecutionPlan,
    dataframes: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    prepared_details: list[pd.DataFrame] = []
    normalized_sources: dict[str, pd.DataFrame] = {}
    detail_by_source: dict[str, pd.DataFrame] = {}

    for source in plan.sources:
        if source not in dataframes:
            raise KeyError(f"계획의 소스 파일이 없습니다: {source}")
        frame = dataframes[source].copy()
        frame = apply_rename(frame, plan.renames)
        classified = classify_rows(frame, plan.summary_row_labels)
        meta_cols = [c for c in (ROW_TYPE_COL, ROW_CONF_COL, ROW_REASONS_COL) if c in classified.columns]
        detail = classified[classified[ROW_TYPE_COL] == "detail"].drop(
            columns=meta_cols,
            errors="ignore",
        )
        detail = _prepare_detail(detail, plan)
        detail_by_source[source] = detail
        prepared_details.append(detail)

        # 소스별 정규화 시트: detail 합산 없이 재구성(소계·요약 재계산)
        rebuilt = rebuild_table_from_details(detail, plan)
        sheet_name = plan.sheet_name_map.get(source) or source
        normalized_sources[_unique_sheet_name(sheet_name, normalized_sources)] = rebuilt

    if not prepared_details:
        raise ValueError("집계할 상세행이 없습니다.")

    combined = pd.concat(prepared_details, ignore_index=True)
    integrated_details = aggregate_by_keys(combined, plan)
    integrated = rebuild_table_from_details(integrated_details, plan)

    sheets = dict(normalized_sources)
    if plan.include_normalized_source_sheets is False:
        sheets = {}
    integrated_name = plan.integrated_sheet_name or "통합"
    sheets[_unique_sheet_name(integrated_name, sheets)] = integrated

    return {
        "integrated": integrated,
        "integrated_details": integrated_details,
        "source_details": detail_by_source,
        "sheets": sheets,
        "plan": plan,
    }


def apply_rename(df: pd.DataFrame, renames: dict[str, str]) -> pd.DataFrame:
    if not renames:
        return df
    mapping = {k: v for k, v in renames.items() if k in df.columns and v}
    if not mapping:
        return df
    result = df.rename(columns=mapping)
    # 충돌 시 뒤쪽 유지
    if result.columns.duplicated().any():
        keep = ~pd.Index(result.columns).duplicated(keep="last")
        result = result.loc[:, keep]
    return result


def classify_rows(
    df: pd.DataFrame,
    summary_labels: list[str],
) -> pd.DataFrame:
    """행을 detail / 비detail 로 분류한다 — row_classify 공통 구현 위임."""
    return classify_row_roles(df, summary_row_labels=summary_labels)


def _prepare_detail(df: pd.DataFrame, plan: ExecutionPlan) -> pd.DataFrame:
    result = df.copy()
    group_col = plan.group_display_column
    if group_col and group_col in result.columns:
        result[group_col] = result[group_col].replace("", pd.NA)
        result[group_col] = result[group_col].ffill()

    for key in plan.group_keys:
        if key not in result.columns:
            continue
        result[key] = result[key].map(_normalize_key_value)

    # drop rows with empty primary identifier if one exists
    id_cols = [k for k in plan.group_keys if k in result.columns]
    if id_cols:
        primary = id_cols[-1] if len(id_cols) > 1 else id_cols[0]
        # Prefer a code-like column: highest uniqueness among keys excluding group display
        candidates = [c for c in id_cols if c != group_col] or id_cols
        primary = candidates[0]
        result = result[result[primary].notna() & (result[primary].astype(str) != "")]

    for col, func in plan.aggregations.items():
        if col not in result.columns:
            result[col] = 0
        if func == "sum":
            result[col] = pd.to_numeric(result[col], errors="coerce").fillna(0)

    return result.reset_index(drop=True)


def aggregate_by_keys(df: pd.DataFrame, plan: ExecutionPlan) -> pd.DataFrame:
    keys = [k for k in plan.group_keys if k in df.columns]
    if not keys:
        raise ValueError("group_keys가 데이터에 없습니다.")

    agg: dict[str, Any] = {}
    for col, func in plan.aggregations.items():
        if col in keys:
            continue
        if col not in df.columns:
            continue
        agg[col] = _pandas_agg(func)

    # label columns not in keys/aggs → first
    for col in df.columns:
        if col in keys or col in agg:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            # numeric not listed as additive → do not sum
            continue
        agg[col] = "first"

    # 첫 등장 순서 보존 (파일 순 concat 기준)
    primary = _primary_key(plan, keys)
    first_order = [
        value
        for value in dict.fromkeys(df[primary].map(_normalize_key_value).tolist())
        if value is not None and str(value) != ""
    ]

    if not agg:
        grouped = df.drop_duplicates(subset=keys).copy()
    else:
        grouped = df.groupby(keys, dropna=False, as_index=False).agg(agg)

    if primary in grouped.columns and first_order:
        grouped["_order"] = grouped[primary].map(_normalize_key_value).map(
            {key: idx for idx, key in enumerate(first_order)}
        )
        grouped = grouped.sort_values("_order", kind="mergesort").drop(columns=["_order"])

    return grouped.reset_index(drop=True)


def _concat_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    if len(frames) == 1:
        return frames[0].copy()
    normalized: list[pd.DataFrame] = []
    columns = list(frames[0].columns)
    for frame in frames:
        part = frame.reindex(columns=columns).copy()
        for col in columns:
            if col not in frame.columns:
                continue
            # avoid all-NA dtype surprises on concat
            if part[col].isna().all():
                part[col] = part[col].astype(object)
        normalized.append(part)
    return pd.concat(normalized, ignore_index=True)


def rebuild_table_from_details(
    details: pd.DataFrame,
    plan: ExecutionPlan,
) -> pd.DataFrame:
    """상세행으로부터 소계·파생 요약행을 넣어 표시용 표를 만든다."""
    if details.empty:
        return details.copy()

    work = details.copy()
    group_col = plan.group_display_column
    if group_col and group_col in work.columns:
        work[group_col] = work[group_col].replace("", pd.NA).ffill()

    additive_cols = [
        col
        for col, func in plan.aggregations.items()
        if func == "sum" and col in work.columns
    ]

    blocks: list[pd.DataFrame] = []
    if group_col and group_col in work.columns:
        # preserve first-seen group order
        groups = list(dict.fromkeys(
            value for value in work[group_col].tolist() if not _is_blank(value)
        ))
        for group_value in groups:
            part = work[work[group_col] == group_value].copy()
            part = _sort_within_group(part, plan, group_col)
            blocks.append(part)
            has_subtotal_spec = any(spec.type == "subtotal" for spec in plan.derived_rows)
            if has_subtotal_spec or not plan.derived_rows:
                subtotal_spec = next(
                    (
                        spec
                        for spec in plan.derived_rows
                        if spec.type == "subtotal"
                        and (spec.group_by in (None, group_col))
                    ),
                    DerivedRowSpec(type="subtotal", label="소계", group_by=group_col),
                )
                blocks.append(
                    _make_summary_frame(
                        part,
                        plan,
                        label=subtotal_spec.label or "소계",
                        additive_cols=additive_cols,
                        place_in=group_col,
                    )
                )
    else:
        blocks.append(_sort_within_group(work, plan, None))

    body = _concat_frames(blocks) if blocks else work

    # footer derived rows
    for spec in plan.derived_rows:
        if spec.type == "subtotal":
            continue
        frame = _apply_derived_summary(work, plan, spec, additive_cols)
        if frame is not None:
            body = _concat_frames([body, frame])

    if plan.blank_repeated_group_labels and group_col and group_col in body.columns:
        body = _blank_repeated(body, group_col)

    body = _order_columns(body, plan)
    return body.reset_index(drop=True)


def _apply_derived_summary(
    details: pd.DataFrame,
    plan: ExecutionPlan,
    spec: DerivedRowSpec,
    additive_cols: list[str],
) -> pd.DataFrame | None:
    place = plan.group_display_column or (plan.group_keys[0] if plan.group_keys else None)
    if place is None:
        return None

    if spec.type == "grand_total" or (
        spec.type == "summary" and (spec.composition or "all") == "all"
    ):
        label = spec.label or "합계"
        return _make_summary_frame(
            details, plan, label=label, additive_cols=additive_cols, place_in=place
        )

    if spec.type == "summary" and spec.composition == "codes":
        code_col = spec.code_column
        if not code_col:
            # pick a group key that looks like codes (not the display group)
            candidates = [k for k in plan.group_keys if k != place]
            code_col = candidates[0] if candidates else plan.group_keys[0]
        if code_col not in details.columns:
            return None
        code_set = {_normalize_key_value(c) for c in spec.codes}
        subset = details[details[code_col].map(_normalize_key_value).isin(code_set)]
        label = spec.label or "요약"
        return _make_summary_frame(
            subset, plan, label=label, additive_cols=additive_cols, place_in=place
        )

    if spec.type == "summary" and spec.composition == "remainder":
        # remainder = all details − previous codes summaries
        code_specs = [
            item
            for item in plan.derived_rows
            if item.type == "summary" and item.composition == "codes"
        ]
        remaining = details
        for code_spec in code_specs:
            code_col = code_spec.code_column
            if not code_col:
                candidates = [k for k in plan.group_keys if k != place]
                code_col = candidates[0] if candidates else plan.group_keys[0]
            if code_col not in remaining.columns:
                continue
            code_set = {_normalize_key_value(c) for c in code_spec.codes}
            remaining = remaining[
                ~remaining[code_col].map(_normalize_key_value).isin(code_set)
            ]
        label = spec.label or "잔여"
        return _make_summary_frame(
            remaining, plan, label=label, additive_cols=additive_cols, place_in=place
        )

    return None


def _make_summary_frame(
    details: pd.DataFrame,
    plan: ExecutionPlan,
    *,
    label: str,
    additive_cols: list[str],
    place_in: str,
) -> pd.DataFrame:
    row: dict[str, Any] = {}
    for col in details.columns:
        row[col] = None
    if place_in in details.columns:
        row[place_in] = label
    for col in additive_cols:
        if col in details.columns:
            row[col] = float(
                pd.to_numeric(details[col], errors="coerce").fillna(0).sum()
            )
    return pd.DataFrame([row], columns=list(details.columns))


def _sort_details(df: pd.DataFrame, plan: ExecutionPlan) -> pd.DataFrame:
    if df.empty:
        return df
    sort_cols = [c for c in (plan.sort_by or []) if c in df.columns]
    if not sort_cols:
        return df.reset_index(drop=True)

    work = df.copy()
    helper_cols: list[str] = []
    for col in sort_cols:
        helper = f"__sort_{col}"
        work[helper] = work[col].map(_sort_key)
        helper_cols.append(helper)
    work = work.sort_values(helper_cols, kind="mergesort")
    return work.drop(columns=helper_cols).reset_index(drop=True)


def _sort_within_group(
    df: pd.DataFrame,
    plan: ExecutionPlan,
    group_col: str | None,
) -> pd.DataFrame:
    """그룹 표시열은 고정하고 식별 키만 정렬한다."""
    if df.empty:
        return df
    keys = [k for k in plan.group_keys if k in df.columns and k != group_col]
    if plan.sort_by:
        keys = [k for k in plan.sort_by if k in df.columns and k != group_col] or keys
    if not keys:
        return df.reset_index(drop=True)
    mini = ExecutionPlan(
        operation=plan.operation,
        sources=plan.sources,
        group_keys=plan.group_keys,
        sort_by=keys,
    )
    return _sort_details(df, mini)


def _primary_key(plan: ExecutionPlan, keys: list[str]) -> str:
    group_col = plan.group_display_column
    candidates = [k for k in keys if k != group_col]
    return candidates[0] if candidates else keys[0]


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    return str(value).strip() == ""


def _blank_repeated(df: pd.DataFrame, column: str) -> pd.DataFrame:
    result = df.copy()
    prev = object()
    values: list[Any] = []
    for value in result[column].tolist():
        text = value
        if pd.isna(text):
            values.append(text)
            continue
        if text == prev:
            values.append(None)
        else:
            values.append(text)
            # summary labels should reset prev so next detail starts fresh
            if _looks_like_summary_label(text):
                prev = object()
            else:
                prev = text
    result[column] = values
    return result


def _looks_like_summary_label(value: object) -> bool:
    norm = normalize_text(_collapse(value))
    return any(token in norm for token in ("소계", "합계", "총계", "subtotal", "total"))


def _order_columns(df: pd.DataFrame, plan: ExecutionPlan) -> pd.DataFrame:
    if not plan.column_order:
        return df
    ordered = [c for c in plan.column_order if c in df.columns]
    rest = [c for c in df.columns if c not in ordered]
    return df.loc[:, ordered + rest]


def _pandas_agg(func: str) -> str:
    mapping = {
        "sum": "sum",
        "first": "first",
        "last": "last",
        "max": "max",
        "min": "min",
        "mean": "mean",
        "count": "count",
    }
    if func not in mapping:
        raise ValueError(f"지원하지 않는 집계: {func}")
    return mapping[func]


def _normalize_key_value(value: object) -> object:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value).rstrip("0").rstrip(".")
    text = str(value).strip()
    if not text:
        return None
    # 121.0 → 121
    try:
        num = float(text.replace(",", ""))
        if num.is_integer():
            return str(int(num))
    except ValueError:
        pass
    return text


def _sort_key(value: object) -> tuple:
    norm = _normalize_key_value(value)
    if norm is None:
        return (2, 0, "")
    text = str(norm)
    if text.isdigit():
        return (0, int(text), "")
    return (1, 0, text)


def _collapse(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if pd.isna(value):
        return ""
    return " ".join(str(value).split())


def _unique_sheet_name(name: str, existing: dict[str, Any]) -> str:
    base = str(name)[:31] or "Sheet"
    if base not in existing:
        return base
    idx = 2
    while True:
        candidate = f"{base[:28]}_{idx}"
        if candidate not in existing:
            return candidate
        idx += 1
