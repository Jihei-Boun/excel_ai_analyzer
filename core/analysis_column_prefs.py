"""집행률·효율 비교용 컬럼 선호 (LLM 계획 보정)."""

from __future__ import annotations

from typing import Any

from core.text_normalize import normalize_text

# 기본(누적) 집행효율: 집행계_합계 ÷ 실행예산_합계
_DEFAULT_EXECUTED = (
    "집행계_합계",
    "집행계",
    "누적집행",
    "집행액",
    "집행금액",
)
_DEFAULT_BUDGET = (
    "실행예산_합계",
    "실행예산",
    "편성예산",
)

# 사용자가 당년을 명시한 경우
_CURRENT_EXECUTED = (
    "당년도집행",
    "집행계_당해집행",
    "당해집행",
    "당해누계",
)
_CURRENT_BUDGET = (
    "당년도예산",
    "실행예산_당해예산",
    "당해예산",
    "계획예산",
)

_EFFICIENCY_TOKENS = (
    "집행효율",
    "집행 효율",
    "집행률",
    "집행율",
    "executionefficiency",
    "executionrate",
)

_CURRENT_YEAR_TOKENS = (
    "당년",
    "당해",
    "올해",
    "금년",
    "당년도",
)


def is_execution_efficiency_prompt(prompt: str) -> bool:
    if not prompt:
        return False
    compact = normalize_text(prompt)
    lowered = prompt.lower()
    return any(normalize_text(tok) in compact or tok.lower() in lowered for tok in _EFFICIENCY_TOKENS)


def asks_current_year_scope(prompt: str) -> bool:
    if not prompt:
        return False
    compact = normalize_text(prompt)
    return any(normalize_text(tok) in compact for tok in _CURRENT_YEAR_TOKENS)


def pick_execution_rate_columns(
    prompt: str,
    columns: list[str] | set[str],
) -> tuple[str, str] | None:
    """(numerator=집행, denominator=예산) 또는 None."""
    colset = {str(c) for c in columns}
    if asks_current_year_scope(prompt):
        num = _first_present(colset, _CURRENT_EXECUTED) or _first_present(
            colset, _DEFAULT_EXECUTED
        )
        den = _first_present(colset, _CURRENT_BUDGET) or _first_present(
            colset, _DEFAULT_BUDGET
        )
    else:
        # 기본 집행효율은 누적(합계). 당년도/계획예산보다 합계열을 우선한다.
        num = _first_present(colset, _DEFAULT_EXECUTED)
        den = _first_present(colset, _DEFAULT_BUDGET)
    if not num or not den or num == den:
        return None
    return num, den


def apply_execution_rate_column_prefs(
    prompt: str,
    data: dict[str, Any],
    columns: list[str],
) -> dict[str, Any]:
    """집행효율·집행률 계획의 분자/분모를 스키마에 맞게 보정한다."""
    if not isinstance(data, dict) or not is_execution_efficiency_prompt(prompt):
        return data
    picked = pick_execution_rate_columns(prompt, columns)
    if not picked:
        return data
    numerator, denominator = picked
    out = dict(data)

    operation = str(out.get("operation") or "").strip()
    if operation in {"group_comparison", "compare_groups", "execution_rate_compare"}:
        out["numerator"] = numerator
        out["denominator"] = denominator
        out.setdefault("rate_name", "집행률")
        note = str(out.get("criteria_note") or "")
        preferred_note = (
            f"집행률 = {numerator} ÷ {denominator} "
            f"({'당년 기준' if asks_current_year_scope(prompt) else '합계 기준'})"
        )
        if "당년도" in note or "계획예산" in note or not note:
            out["criteria_note"] = preferred_note

    steps = out.get("steps")
    if isinstance(steps, list):
        out["steps"] = _rewrite_steps_for_rate(steps, numerator, denominator)

    return out


def _first_present(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        if name in columns:
            return name
    # 부분 일치 (예: 무언가_집행계_합계)
    for name in candidates:
        target = normalize_text(name)
        for col in columns:
            if target and target == normalize_text(col):
                return col
    return None


def _rewrite_steps_for_rate(
    steps: list[Any],
    numerator: str,
    denominator: str,
) -> list[Any]:
    rewritten: list[Any] = []
    for step in steps:
        if not isinstance(step, dict):
            rewritten.append(step)
            continue
        item = dict(step)
        op = str(item.get("op") or item.get("operation") or "")
        if op == "ratio_of_aggregates":
            item["numerator"] = numerator
            item["denominator"] = denominator
            item.setdefault("name", "집행률")
        elif op == "aggregate":
            metrics = item.get("metrics") or []
            if isinstance(metrics, list):
                item["metrics"] = _ensure_metric_columns(metrics, [denominator, numerator])
        elif op == "compare_groups":
            metrics = item.get("metrics") or []
            if isinstance(metrics, list):
                rate_name = "집행률"
                for prev in steps:
                    if isinstance(prev, dict) and str(prev.get("op") or "") == "ratio_of_aggregates":
                        rate_name = str(prev.get("name") or rate_name)
                        break
                wanted = [denominator, numerator, rate_name]
                # 문자열 리스트인 경우
                if metrics and all(isinstance(m, str) for m in metrics):
                    item["metrics"] = wanted
                item["rate_columns"] = [rate_name]
        rewritten.append(item)
    return rewritten


def _ensure_metric_columns(
    metrics: list[Any],
    required: list[str],
) -> list[Any]:
    present: set[str] = set()
    out: list[Any] = []
    for item in metrics:
        if isinstance(item, str):
            present.add(item)
            out.append(item)
        elif isinstance(item, dict):
            col = str(item.get("column") or item.get("name") or "")
            if col:
                present.add(col)
            out.append(item)
    for col in required:
        if col not in present:
            out.append({"column": col, "fn": "sum"})
            present.add(col)
    return out
