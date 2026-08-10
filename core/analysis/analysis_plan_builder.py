"""LLM 채팅 분석 계획 생성."""

from __future__ import annotations

import json
from typing import Any, Callable

import pandas as pd

from core.analysis.analysis_plan_types import AnalysisPlan, analysis_plan_from_dict
from core.analysis.analysis_column_prefs import apply_safety_column_normalization
from core.analysis.analysis_plan_contract import PLANNER_SYSTEM_PROMPT
from core.llm_client import chat_json
from core.profile_loader import active_profile, roles_for
from core.schema.row_classify import ROW_TYPE_COL, classification_summary, row_type_distribution
from core.integrate.schema_infer import semantic_hints_text
from core.io.text_normalize import normalize_text

_MAX_SAMPLE_VALUES = 6

# 컬럼명에서 추정하는 범용 role hint (profile roles와 병합)
_NAME_ROLE_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("당년도", "당해", "current", "당기", "실적", "actual"), "actual"),
    (("누적", "ytd", "cumulative", "누계"), "cumulative_actual"),
    (("목표", "target", "계획", "plan", "planned"), "target"),
    (("전년", "prior", "작년"), "prior_period"),
    (("안전", "safety", "threshold", "허용", "limit"), "threshold"),
    (("최소", "min", "minimum", "하한", "reorder"), "minimum"),
    (("최대", "max", "maximum", "상한"), "maximum"),
    (("기준", "baseline", "base"), "baseline"),
    (("현재", "current_qty", "current_value", "재고수량", "stock", "qty", "수량"), "current"),
    (("평균", "mean", "avg"), "average_metric"),
)


def _plan_system_prompt(
    *,
    profile_name: str | None = None,
) -> str:
    from core.profile_loader import plan_language_note_for

    profile = active_profile(
        profile_name=profile_name,
    )
    guidance = str(profile.get("plan_guidance") or "").strip()
    lang_note = plan_language_note_for(profile_name=profile_name)
    parts = [PLANNER_SYSTEM_PROMPT]
    if lang_note:
        parts.append(lang_note)
    if guidance:
        parts.append(f"Domain guidance (hints only, not hard rules): {guidance}")
    return "\n\n".join(parts)


def build_planner_column_inventory(
    df: pd.DataFrame,
    *,
    profile_name: str | None = None,
    max_samples: int = _MAX_SAMPLE_VALUES,
) -> list[dict[str, Any]]:
    """Planner용 컬럼 inventory (sample 제한, role_hints는 강제 아님)."""
    roles = roles_for(profile_name=profile_name)
    role_index: dict[str, list[str]] = {}
    role_map = {
        "group_columns": "group_candidate",
        "label_columns": "label_candidate",
        "metric_numerator": "numerator_candidate",
        "metric_denominator": "denominator_candidate",
        "metric_remaining": "remaining_candidate",
        "key_metrics": "key_metric",
    }
    for role_key, hint in role_map.items():
        for col in roles.get(role_key) or ():
            role_index.setdefault(str(col), []).append(hint)

    columns: list[dict[str, Any]] = []
    for col in df.columns:
        name = str(col)
        if name.startswith("_"):
            continue
        series = df[col]
        non_null = series.dropna()
        null_ratio = float(series.isna().mean()) if len(series) else 0.0
        is_num = pd.api.types.is_numeric_dtype(series) or _mostly_numeric(non_null)
        is_dt = pd.api.types.is_datetime64_any_dtype(series)
        if is_dt:
            dtype = "datetime"
        elif is_num:
            dtype = "numeric"
        else:
            dtype = "categorical"

        sample = [_jsonable(v) for v in non_null.head(max_samples).tolist()]
        n_non_null = int(len(non_null))
        unique_count = int(non_null.nunique())
        unique_ratio = round(unique_count / n_non_null, 4) if n_non_null else 0.0
        entry: dict[str, Any] = {
            "name": name,
            "dtype": dtype,
            "null_ratio": round(null_ratio, 4),
            "unique_count": unique_count,
            "unique_ratio": unique_ratio,
            "sample_values": sample,
        }
        # Grain hint for ranking: repeated values → entity; nearly unique → row-like
        if unique_ratio >= 0.95 and n_non_null >= 4:
            entry["grain_hint"] = "row_id_like"
        elif unique_ratio <= 0.35 and n_non_null >= 4:
            entry["grain_hint"] = "repeated_entity_candidate"
        hints = list(role_index.get(name) or [])
        for tokens, hint in _NAME_ROLE_PATTERNS:
            norm = normalize_text(name)
            if any(normalize_text(tok) in norm for tok in tokens):
                if hint not in hints:
                    hints.append(hint)
        if hints:
            entry["role_hints"] = hints
        columns.append(entry)
    return columns


def build_planner_row_context(classified_df: pd.DataFrame | None, df: pd.DataFrame) -> dict[str, Any]:
    """row level 분포만 요약 (전체 행 데이터는 넣지 않음)."""
    frame = classified_df if classified_df is not None else df
    if frame is None or frame.empty:
        return {"row_types": {}, "levels_detected": []}
    if ROW_TYPE_COL in frame.columns:
        dist = row_type_distribution(frame)
    else:
        dist = {}
    levels = [k for k, v in dist.items() if int(v) > 0]
    return {
        "row_types": {str(k): int(v) for k, v in dist.items()},
        "levels_detected": levels,
        "note": (
            "When aggregating, choose one row level explicitly to avoid double-counting "
            "(detail vs subtotal/total/footer)."
        ),
    }


def build_analysis_plan(
    prompt: str,
    df: pd.DataFrame,
    *,
    base_url: str,
    model: str,
    classified_df: pd.DataFrame | None = None,
    profile_name: str | None = None,
    previous_errors: list[str] | None = None,
    chat_json_fn: Callable[..., dict[str, Any]] = chat_json,
) -> AnalysisPlan:
    """사용자 요청 + 스키마 inventory → 원자 step 분석 계획.

    Phase 5: safety 컬럼명 정규화만 적용. 의미 rewrite 없음.
    """
    column_inventory = build_planner_column_inventory(df, profile_name=profile_name)
    row_context = build_planner_row_context(classified_df, df)
    row_summary = classification_summary(classified_df if classified_df is not None else df)
    columns = [str(c) for c in df.columns if not str(c).startswith("_")]

    system = _plan_system_prompt(
        profile_name=profile_name,
    )

    user_parts = [
        f"User request:\n{prompt}",
        f"Available columns:\n{json.dumps(columns, ensure_ascii=False)}",
        (
            "Column inventory (samples are limited; role_hints/grain_hint are optional "
            "semantic hints only — never invent columns):\n"
            f"{json.dumps(column_inventory, ensure_ascii=False, indent=2)}\n"
            "Grain notes: grain_hint=repeated_entity_candidate (low unique_ratio) usually "
            "means entity-level ranking needs aggregate before sort. "
            "grain_hint=row_id_like (unique_ratio≈1) often means row-level sort→limit is enough. "
            "After aggregate, sort/select the source metric column name — not X_합계/X_sum."
        ),
        f"Row levels:\n{json.dumps(row_context, ensure_ascii=False, indent=2)}",
        f"Row type summary:\n{json.dumps(row_summary, ensure_ascii=False, indent=2)}",
        (
            "Return JSON using either atomic steps[] matching the contracts, "
            "or one compact high-level operation from the system prompt. "
            "Always include required fields (especially aggregate.metrics[].fn)."
        ),
    ]
    hint = semantic_hints_text(
        profile_name=profile_name,
    )
    if hint:
        user_parts.append(hint)
    role_hint_text = _role_semantic_hint_text(df, profile_name=profile_name)
    if role_hint_text:
        user_parts.append(role_hint_text)
    if previous_errors:
        user_parts.append(
            "Previous plan failed validation. Fix these issues and regenerate "
            "a corrected AnalysisPlan. Do NOT invent columns. "
            "Do not repeat the previous invalid plan unchanged. "
            "Hints list candidates only — do not treat them as mandatory answers:\n"
            + "\n".join(f"- {err}" for err in previous_errors)
        )

    data = chat_json_fn(
        "\n\n".join(user_parts),
        system=system,
        base_url=base_url,
        model=model,
    )
    if not isinstance(data, dict):
        raise ValueError("분석 계획이 객체가 아닙니다.")

    data = apply_safety_column_normalization(data, columns)

    profile = active_profile(
        profile_name=profile_name,
    )
    plan = analysis_plan_from_dict(
        data, available_columns=columns, profile_name=profile_name
    )
    plan.footer_labels = [str(x) for x in (profile.get("footer_labels") or ())]
    return plan


def _role_semantic_hint_text(
    df: pd.DataFrame,
    *,
    profile_name: str | None = None,
) -> str:
    roles = roles_for(profile_name=profile_name)
    present = {str(c) for c in df.columns}
    lines: list[str] = []
    for col in roles.get("metric_denominator") or ():
        if col in present:
            lines.append(
                f"- `{col}` role_hint: denominator_candidate / planned_or_budget_like"
            )
    for col in roles.get("metric_numerator") or ():
        if col in present:
            lines.append(
                f"- `{col}` role_hint: numerator_candidate / actual_or_executed_like"
            )
    for col in roles.get("group_columns") or ():
        if col in present:
            lines.append(f"- `{col}` role_hint: group_candidate")
    # name-based siblings
    for col in df.columns:
        name = str(col)
        if name.startswith("_"):
            continue
        for tokens, hint in _NAME_ROLE_PATTERNS:
            norm = normalize_text(name)
            if any(normalize_text(tok) in norm for tok in tokens):
                lines.append(f"- `{name}` role_hint: {hint}")
                break
    if not lines:
        return ""
    # dedupe preserve order
    seen: set[str] = set()
    unique: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    # Generic relation candidates only — no operation prescription (avoids mention bias).
    role_tokens = " ".join(unique).lower()
    has_actualish = any(
        tok in role_tokens
        for tok in ("actual", "current", "numerator", "executed")
    )
    has_thresholdish = any(
        tok in role_tokens
        for tok in ("target", "threshold", "minimum", "maximum", "baseline", "planned", "denominator")
    )
    if has_actualish and has_thresholdish:
        unique.append(
            "- possible numeric relationships (hints only): "
            "actual/current vs target/threshold/minimum/maximum/baseline/planned"
        )
    return (
        "Optional semantic role_hints (do NOT hardcode; use only if they fit the request "
        "and actual columns):\n" + "\n".join(unique[:18])
    )


def _mostly_numeric(series: pd.Series) -> bool:
    if series.empty:
        return False
    coerced = pd.to_numeric(series, errors="coerce")
    return float(coerced.notna().mean()) >= 0.5


def _jsonable(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:  # noqa: BLE001
            return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
