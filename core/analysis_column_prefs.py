"""분석 계획 컬럼 선호·보정 (집행률 / 항목 탐색)."""

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

_CARRYOVER_TOKENS = ("이월예산", "이월", "실행예산_이월")
_ZERO_EXEC_TOKENS = ("당해집행", "당년도집행", "집행계_당해")
_FIND_TOKENS = ("찾", "골라", "추출", "필터", "없는", "미집행")


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


def is_carryover_no_current_exec_prompt(prompt: str) -> bool:
    """이월예산은 있는데 당해집행이 없는 항목 탐색 질의."""
    if not prompt:
        return False
    compact = normalize_text(prompt)
    has_carry = any(normalize_text(t) in compact for t in _CARRYOVER_TOKENS)
    has_curr = any(normalize_text(t) in compact for t in _ZERO_EXEC_TOKENS)
    has_find = any(t in prompt for t in _FIND_TOKENS) or "없" in prompt
    return has_carry and has_curr and has_find


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


def apply_analysis_column_prefs(
    prompt: str,
    data: dict[str, Any],
    columns: list[str],
) -> dict[str, Any]:
    """도메인 질의에 맞춰 계획 JSON을 보정한다."""
    data = apply_top_n_per_group_prefs(prompt, data, columns)
    if str((data or {}).get("operation") or "") in {
        "top_n_per_group",
        "top_per_group",
        "rank_per_group",
    }:
        return data
    data = apply_rate_vs_mean_prefs(prompt, data, columns)
    if str((data or {}).get("operation") or "") in {
        "rate_vs_mean",
        "execution_rate_vs_mean",
    }:
        return data
    data = apply_provisional_share_prefs(prompt, data, columns)
    if str((data or {}).get("operation") or "") == "find_items" and (data or {}).get(
        "rate_name"
    ):
        return data
    data = apply_find_items_column_prefs(prompt, data, columns)
    if str((data or {}).get("operation") or "") == "find_items":
        return data
    return apply_execution_rate_column_prefs(prompt, data, columns)


def is_provisional_share_prompt(prompt: str) -> bool:
    """가집행이 있는 항목의 당해누계 대비 비중 질의."""
    if not prompt:
        return False
    compact = normalize_text(prompt)
    has_prov = "가집행" in compact
    has_base = "당해누계" in compact
    has_share = any(tok in prompt for tok in ("비중", "비율", "대비", "차지"))
    return bool(has_prov and has_base and has_share)


def apply_provisional_share_prefs(
    prompt: str,
    data: dict[str, Any],
    columns: list[str],
) -> dict[str, Any]:
    """가집행>0 항목에 가집행÷당해누계 비중(%) 열을 붙인다."""
    if not isinstance(data, dict) or not is_provisional_share_prompt(prompt):
        return data
    colset = {str(c) for c in columns}
    prov = _first_present(colset, ("가집행금액", "가집행"))
    base = _first_present(colset, ("당해누계",))
    if not prov or not base or prov == base:
        return data

    labels = [c for c in ("비목분류", "비용명_2", "비용명") if c in colset]
    out = dict(data)
    out["operation"] = "find_items"
    out["numeric_filters"] = [{"column": prov, "op": "gt", "value": 0}]
    out["numerator"] = prov
    out["denominator"] = base
    out["rate_name"] = "비중"
    out["sort_by"] = ["비중"]
    out["ascending"] = [True]
    out["output_columns"] = [*labels, prov, base, "비중"]
    out["interpret"] = True if "interpret" not in out else bool(out.get("interpret"))
    out["criteria_note"] = (
        f"{prov} > 0인 항목만 골라 {prov} ÷ {base} 비중(%)을 계산했습니다."
    )
    out.pop("steps", None)
    return out


def is_rate_vs_mean_prompt(prompt: str) -> bool:
    """집행률(비율)을 구한 뒤 평균보다 낮/높은 항목 질의."""
    if not prompt:
        return False
    has_rate = is_execution_efficiency_prompt(prompt) or ("비율" in prompt)
    has_mean = "평균" in prompt
    has_cmp = any(
        tok in prompt
        for tok in ("낮은", "높은", "미만", "이상", "아래", "위인", "작은", "큰")
    )
    return bool(has_rate and has_mean and has_cmp)


def _wants_table_only(prompt: str) -> bool:
    has_table = any(tok in prompt for tok in ("표로", "표 ", "표만", "보여줘", "리스트", "목록"))
    has_explain = any(tok in prompt for tok in ("의미", "설명", "해석"))
    return has_table and not has_explain


def apply_rate_vs_mean_prefs(
    prompt: str,
    data: dict[str, Any],
    columns: list[str],
) -> dict[str, Any]:
    """집행률 평균 비교 질의를 rate_vs_mean + 합계 열로 강제한다."""
    if not isinstance(data, dict) or not is_rate_vs_mean_prompt(prompt):
        return data
    picked = pick_execution_rate_columns(prompt, columns)
    if not picked:
        return data
    numerator, denominator = picked
    relation = "above" if any(
        tok in prompt for tok in ("높은", "이상", "초과", "큰")
    ) and not any(tok in prompt for tok in ("낮은", "미만", "아래", "작은")) else "below"

    colset = {str(c) for c in columns}
    labels = [c for c in ("비용명", "비용명_2", "비목분류") if c in colset]
    out = dict(data)
    out["operation"] = "rate_vs_mean"
    out["numerator"] = numerator
    out["denominator"] = denominator
    out["rate_name"] = "집행률"
    out["relation"] = relation
    out["output_columns"] = [*labels, denominator, numerator, "집행률"]
    out["interpret"] = False if _wants_table_only(prompt) else bool(out.get("interpret", False))
    out["criteria_note"] = (
        f"집행률 = {numerator} ÷ {denominator} (분모 0 제외). "
        f"산술평균보다 {'낮은' if relation == 'below' else '높은'} 항목만 표시."
    )
    out.pop("steps", None)
    return out


def is_top_n_per_group_prompt(prompt: str) -> bool:
    """그룹(비목)별 대표 항목(가장 큰/작은·하나씩) 질의."""
    if not prompt:
        return False
    has_group = any(
        tok in prompt
        for tok in ("별로", "별 ", "분류별", "비목별", "그룹별", "비목분류별")
    ) or ("별" in prompt and any(tok in prompt for tok in ("비목", "분류", "그룹")))
    has_pick = any(
        tok in prompt
        for tok in ("가장", "하나씩", "하나 씩", "상위", "하위", "최대", "최소")
    )
    has_metric = any(
        tok in prompt
        for tok in ("잔액", "집행", "예산", "금액", "비용명", "항목")
    )
    return bool(has_group and has_pick and has_metric)


def pick_balance_column(prompt: str, columns: list[str]) -> str | None:
    """잔액 질의 시 열 선택. 당해 명시 시에만 당해잔액."""
    colset = {str(c) for c in columns}
    wants_current = any(tok in prompt for tok in _CURRENT_YEAR_TOKENS) and (
        "잔액" in prompt
    )
    if wants_current:
        picked = _first_present(
            colset,
            ("예산잔액_당해잔액", "당해잔액", "예산잔액_합계", "예산잔액"),
        )
    else:
        picked = _first_present(
            colset,
            ("예산잔액_합계", "예산잔액", "예산잔액_당해잔액", "당해잔액"),
        )
    return picked


def apply_top_n_per_group_prefs(
    prompt: str,
    data: dict[str, Any],
    columns: list[str],
) -> dict[str, Any]:
    """비목별 잔액 최대(또는 최소) 항목 하나씩 → top_n_per_group."""
    if not isinstance(data, dict) or not is_top_n_per_group_prompt(prompt):
        return data
    colset = {str(c) for c in columns}
    group_col = _first_present(colset, ("비목분류", "비목", "분류"))
    if not group_col:
        return data

    value_col: str | None = None
    if "잔액" in prompt:
        value_col = pick_balance_column(prompt, columns)
    if not value_col:
        # 잔액이 아니면 LLM/기존 plan의 value를 유지하되 없으면 합계 잔액 시도
        candidate = str(
            data.get("value_column") or data.get("metric") or ""
        ).strip()
        if candidate in colset:
            value_col = candidate
        else:
            value_col = pick_balance_column(prompt, columns) or _first_present(
                colset,
                ("예산잔액_합계", "집행계_합계", "실행예산_합계"),
            )
    if not value_col:
        return data

    ascending = any(tok in prompt for tok in ("작", "낮", "최소", "하위")) and not any(
        tok in prompt for tok in ("큰", "높", "최대", "상위")
    )
    labels = [c for c in ("비목분류", "비용명_2", "비용명") if c in colset]
    out = dict(data)
    out["operation"] = "top_n_per_group"
    out["group_column"] = group_col
    out["value_column"] = value_col
    out["n"] = 1
    out["ascending"] = ascending
    out["output_columns"] = [*labels, value_col]
    out["interpret"] = (
        False if _wants_table_only(prompt) else bool(out.get("interpret", False))
    )
    out["criteria_note"] = (
        f"{group_col}별로 {value_col}이(가) 가장 "
        f"{'작은' if ascending else '큰'} 세부 항목 1개씩."
    )
    out.pop("steps", None)
    return out


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


def apply_find_items_column_prefs(
    prompt: str,
    data: dict[str, Any],
    columns: list[str],
) -> dict[str, Any]:
    """이월>0 & 당해집행=0 탐색을 find_items + 최소 열로 보정한다."""
    if not isinstance(data, dict) or not is_carryover_no_current_exec_prompt(prompt):
        return data
    colset = {str(c) for c in columns}
    carry = _first_present(
        colset,
        ("실행예산_이월예산", "이월예산", "실행예산_이월"),
    )
    curr_exec = _first_present(
        colset,
        ("집행계_당해집행", "당년도집행", "당해집행"),
    )
    if not carry or not curr_exec:
        return data

    out = dict(data)
    out["operation"] = "find_items"
    out["numeric_filters"] = [
        {"column": carry, "op": "gt", "value": 0},
        {"column": curr_exec, "op": "eq", "value": 0},
    ]
    out["sort_by"] = [carry]
    out["ascending"] = [False]
    labels = [c for c in ("비목분류", "비용명_2", "비용명") if c in colset]
    extras = [
        c
        for c in ("집행계_합계", "집행계_이월집행", "예산잔액_합계")
        if c in colset and c not in {carry, curr_exec}
    ][:2]
    out["output_columns"] = [*labels, carry, curr_exec, *extras]
    out["interpret"] = True
    out["criteria_note"] = (
        f"{carry} > 0 이고 {curr_exec} = 0인 세부 항목을 "
        "관련 열만 골라 이월예산 큰 순으로 정렬했습니다."
    )
    # steps가 있으면 고수준으로 다시 컴파일되도록 비움
    out.pop("steps", None)
    return out


def _first_present(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        if name in columns:
            return name
    for name in candidates:
        target = normalize_text(name)
        for col in columns:
            if target and target == normalize_text(col):
                return col
    for name in candidates:
        target = normalize_text(name)
        for col in columns:
            if target and target in normalize_text(col):
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
