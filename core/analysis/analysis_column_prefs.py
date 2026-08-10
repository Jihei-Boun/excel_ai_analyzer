"""분석 계획 컬럼명 safety 정규화.

Phase 5: 의미 rewrite(`apply_*_prefs`)는 제거됨.
의미를 바꾸지 않는 canonicalization만 유지한다.
"""

from __future__ import annotations

from typing import Any

from core.io.text_normalize import normalize_text

# Phase 5 최종: rewrite 제거, safety만 유지
PREFS_REWRITE_CLASSIFICATION: dict[str, str] = {
    "apply_safety_column_normalization": "C",
    "legacy_apply_*_prefs": "removed_phase5",
}


def apply_safety_column_normalization(
    data: dict[str, Any],
    columns: list[str],
) -> dict[str, Any]:
    """의미를 바꾸지 않는 컬럼명 정규화만 수행한다.

    - exact match
    - whitespace collapse match
    - case-insensitive match (ASCII)

    존재하지 않는 이름을 다른 의미의 preferred 컬럼으로 바꾸지 않는다.
    """
    if not isinstance(data, dict):
        return data
    colset = {str(c) for c in columns}
    by_norm = {normalize_text(c): c for c in colset}
    by_fold = {normalize_text(c).replace("_", ""): c for c in colset}
    by_lower = {c.lower(): c for c in colset}

    def _canon(name: Any) -> Any:
        if not isinstance(name, str) or not name.strip():
            return name
        if name in colset:
            return name
        norm = normalize_text(name)
        if norm in by_norm:
            return by_norm[norm]
        fold = norm.replace("_", "")
        if fold in by_fold:
            return by_fold[fold]
        low = name.lower()
        if low in by_lower:
            return by_lower[low]
        return name

    out = dict(data)
    for key in (
        "group_column",
        "numerator",
        "denominator",
        "x_column",
        "y_column",
        "label_column",
        "value_column",
        "left",
        "right",
        "rate_name",
    ):
        if key in out:
            out[key] = _canon(out[key])

    for key in ("group_by", "dimension_columns", "output_columns", "sort_by", "value_columns"):
        val = out.get(key)
        if isinstance(val, list):
            out[key] = [_canon(v) for v in val]
        elif isinstance(val, str):
            out[key] = _canon(val)

    filters = out.get("numeric_filters")
    if isinstance(filters, list):
        fixed = []
        for item in filters:
            if isinstance(item, dict):
                row = dict(item)
                if "column" in row:
                    row["column"] = _canon(row["column"])
                fixed.append(row)
            else:
                fixed.append(item)
        out["numeric_filters"] = fixed

    steps = out.get("steps")
    if isinstance(steps, list):
        out["steps"] = [_normalize_step_columns(step, _canon) for step in steps]
    return out


def _normalize_step_columns(step: Any, canon) -> Any:  # noqa: ANN001
    if not isinstance(step, dict):
        return step
    item = dict(step)
    for key in (
        "group_column",
        "numerator",
        "denominator",
        "name",
        "column",
        "x_column",
        "y_column",
        "value_column",
        "denominator_column",
        "numerator_column",
    ):
        if key in item:
            item[key] = canon(item[key])
    for key in ("group_by", "columns", "by", "metrics", "rate_columns"):
        val = item.get(key)
        if isinstance(val, list):
            new_list = []
            for entry in val:
                if isinstance(entry, str):
                    new_list.append(canon(entry))
                elif isinstance(entry, dict):
                    row = dict(entry)
                    if "column" in row:
                        row["column"] = canon(row["column"])
                    new_list.append(row)
                else:
                    new_list.append(entry)
            item[key] = new_list
    return item
