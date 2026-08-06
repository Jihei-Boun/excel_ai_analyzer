"""분석 계획 컬럼 선호·보정 — 프로필 column_prefs 기반."""

from __future__ import annotations

from typing import Any

from core.profile_loader import active_profile, column_prefs_for
from core.text_normalize import normalize_text


def _prefs(
    *,
    profile_name: str | None = None,
) -> dict[str, Any]:
    return column_prefs_for(
        profile_name=profile_name,
    )


def _tok(prefs: dict[str, Any], key: str) -> tuple[str, ...]:
    value = prefs.get(key) or ()
    if isinstance(value, str):
        return (value,) if value else ()
    return tuple(str(x) for x in value)


def _str(prefs: dict[str, Any], key: str, default: str = "") -> str:
    value = prefs.get(key)
    if value is None or value == ():
        return default
    return str(value)


def is_execution_efficiency_prompt(
    prompt: str,
    *,
    profile_name: str | None = None,
) -> bool:
    if not prompt:
        return False
    prefs = _prefs(profile_name=profile_name)
    tokens = _tok(prefs, "efficiency_tokens")
    if not tokens:
        return False
    compact = normalize_text(prompt)
    lowered = prompt.lower()
    return any(normalize_text(tok) in compact or tok.lower() in lowered for tok in tokens)


def asks_current_year_scope(
    prompt: str,
    *,
    profile_name: str | None = None,
) -> bool:
    if not prompt:
        return False
    prefs = _prefs(profile_name=profile_name)
    tokens = _tok(prefs, "current_year_tokens")
    if not tokens:
        return False
    compact = normalize_text(prompt)
    return any(normalize_text(tok) in compact for tok in tokens)


def is_carryover_no_current_exec_prompt(
    prompt: str,
    *,
    profile_name: str | None = None,
) -> bool:
    """이월예산은 있는데 당해집행이 없는 항목 탐색 질의."""
    if not prompt:
        return False
    prefs = _prefs(profile_name=profile_name)
    carry_tokens = _tok(prefs, "carryover_tokens")
    zero_tokens = _tok(prefs, "zero_exec_tokens")
    find_tokens = _tok(prefs, "find_tokens")
    if not carry_tokens or not zero_tokens:
        return False
    compact = normalize_text(prompt)
    has_carry = any(normalize_text(t) in compact for t in carry_tokens)
    has_curr = any(normalize_text(t) in compact for t in zero_tokens)
    has_find = any(t in prompt for t in find_tokens) or "없" in prompt
    return has_carry and has_curr and has_find


def pick_execution_rate_columns(
    prompt: str,
    columns: list[str] | set[str],
    *,
    profile_name: str | None = None,
) -> tuple[str, str] | None:
    """(numerator, denominator) 또는 None."""
    prefs = _prefs(profile_name=profile_name)
    colset = {str(c) for c in columns}
    default_num = _tok(prefs, "default_numerator")
    default_den = _tok(prefs, "default_denominator")
    current_num = _tok(prefs, "current_numerator")
    current_den = _tok(prefs, "current_denominator")
    if asks_current_year_scope(
        prompt, profile_name=profile_name
    ):
        num = _first_present(colset, current_num) or _first_present(colset, default_num)
        den = _first_present(colset, current_den) or _first_present(colset, default_den)
    else:
        num = _first_present(colset, default_num)
        den = _first_present(colset, default_den)
    if not num or not den or num == den:
        return None
    return num, den


def apply_analysis_column_prefs(
    prompt: str,
    data: dict[str, Any],
    columns: list[str],
    *,
    profile_name: str | None = None,
    enable_column_prefs: bool | None = None,
    category_labels: list[str] | None = None,
) -> dict[str, Any]:
    """도메인 질의에 맞춰 계획 JSON을 보정한다.

    활성 프로필의 enable_column_prefs=true일 때만 동작한다.
    """
    if enable_column_prefs is None:
        enable_column_prefs = bool(
            active_profile(
                profile_name=profile_name,
            ).get("enable_column_prefs")
        )
    if not enable_column_prefs:
        return data

    kw = {"profile_name": profile_name}
    data = apply_split_by_difference_prefs(prompt, data, columns, **kw)
    if str((data or {}).get("operation") or "") in {
        "split_by_difference",
        "increase_decrease_split",
        "budget_change_split",
    }:
        return data
    data = apply_top_n_per_group_prefs(prompt, data, columns, **kw)
    if str((data or {}).get("operation") or "") in {
        "top_n_per_group",
        "top_per_group",
        "rank_per_group",
    }:
        return data
    data = apply_rate_vs_mean_prefs(prompt, data, columns, **kw)
    if str((data or {}).get("operation") or "") in {
        "rate_vs_mean",
        "execution_rate_vs_mean",
    }:
        return data
    data = apply_provisional_share_prefs(prompt, data, columns, **kw)
    if str((data or {}).get("operation") or "") == "find_items" and (data or {}).get(
        "rate_name"
    ):
        return data
    data = apply_find_items_column_prefs(prompt, data, columns, **kw)
    if str((data or {}).get("operation") or "") == "find_items":
        return data
    data = apply_group_efficiency_compare_prefs(
        prompt, data, columns, category_labels=category_labels, **kw
    )
    if str((data or {}).get("operation") or "") in {
        "group_comparison",
        "compare_groups",
        "execution_rate_compare",
    } and (data or {}).get("groups"):
        return data
    return apply_execution_rate_column_prefs(prompt, data, columns, **kw)


def is_provisional_share_prompt(
    prompt: str,
    *,
    profile_name: str | None = None,
) -> bool:
    """가집행 비중 질의 등 — prefs에 provisional 컬럼이 있을 때만."""
    if not prompt:
        return False
    prefs = _prefs(profile_name=profile_name)
    if not _tok(prefs, "provisional_columns") or not _tok(prefs, "provisional_base_columns"):
        return False
    compact = normalize_text(prompt)
    # 토큰은 프로필 후보의 대표 문자열을 사용 (하드코딩 도메인어 없음)
    prov_cols = _tok(prefs, "provisional_columns")
    base_cols = _tok(prefs, "provisional_base_columns")
    has_prov = any(normalize_text(c)[:3] in compact for c in prov_cols if c)
    has_base = any(normalize_text(c)[:4] in compact for c in base_cols if c)
    has_share = any(tok in prompt for tok in ("비중", "비율", "대비", "차지"))
    return bool(has_prov and has_base and has_share)


def apply_provisional_share_prefs(
    prompt: str,
    data: dict[str, Any],
    columns: list[str],
    *,
    profile_name: str | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict) or not is_provisional_share_prompt(
        prompt, profile_name=profile_name
    ):
        return data
    prefs = _prefs(profile_name=profile_name)
    colset = {str(c) for c in columns}
    prov = _first_present(colset, _tok(prefs, "provisional_columns"))
    base = _first_present(colset, _tok(prefs, "provisional_base_columns"))
    if not prov or not base or prov == base:
        return data

    labels = [c for c in _tok(prefs, "label_columns") if c in colset]
    share_name = _str(prefs, "share_name", "비중")
    out = dict(data)
    out["operation"] = "find_items"
    out["numeric_filters"] = [{"column": prov, "op": "gt", "value": 0}]
    out["numerator"] = prov
    out["denominator"] = base
    out["rate_name"] = share_name
    out["sort_by"] = [share_name]
    out["ascending"] = [True]
    out["output_columns"] = [*labels, prov, base, share_name]
    out["interpret"] = True if "interpret" not in out else bool(out.get("interpret"))
    out["criteria_note"] = (
        f"{prov} > 0인 항목만 골라 {prov} ÷ {base} {share_name}(%)을 계산했습니다."
    )
    out.pop("steps", None)
    return out


def is_rate_vs_mean_prompt(
    prompt: str,
    *,
    profile_name: str | None = None,
) -> bool:
    if not prompt:
        return False
    has_rate = is_execution_efficiency_prompt(
        prompt, profile_name=profile_name
    ) or ("비율" in prompt)
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
    *,
    profile_name: str | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict) or not is_rate_vs_mean_prompt(
        prompt, profile_name=profile_name
    ):
        return data
    picked = pick_execution_rate_columns(
        prompt, columns, profile_name=profile_name
    )
    if not picked:
        return data
    numerator, denominator = picked
    relation = "above" if any(
        tok in prompt for tok in ("높은", "이상", "초과", "큰")
    ) and not any(tok in prompt for tok in ("낮은", "미만", "아래", "작은")) else "below"

    prefs = _prefs(profile_name=profile_name)
    colset = {str(c) for c in columns}
    label_order = _tok(prefs, "rate_label_columns") or _tok(prefs, "label_columns")
    labels = [c for c in label_order if c in colset]
    rate_name = _str(prefs, "rate_name", "비율")
    out = dict(data)
    out["operation"] = "rate_vs_mean"
    out["numerator"] = numerator
    out["denominator"] = denominator
    out["rate_name"] = rate_name
    out["relation"] = relation
    out["output_columns"] = [*labels, denominator, numerator, rate_name]
    out["interpret"] = False if _wants_table_only(prompt) else bool(out.get("interpret", False))
    out["criteria_note"] = (
        f"{rate_name} = {numerator} ÷ {denominator} (분모 0 제외). "
        f"산술평균보다 {'낮은' if relation == 'below' else '높은'} 항목만 표시."
    )
    out.pop("steps", None)
    return out


def is_split_by_difference_prompt(
    prompt: str,
    *,
    profile_name: str | None = None,
) -> bool:
    if not prompt:
        return False
    prefs = _prefs(profile_name=profile_name)
    if not _tok(prefs, "plan_columns") or not _tok(prefs, "exec_columns"):
        return False
    has_up = any(tok in prompt for tok in ("늘어난", "증가", "증액", "커진"))
    has_down = any(tok in prompt for tok in ("줄어든", "감소", "감액", "작아진"))
    has_split = any(
        tok in prompt for tok in ("나눠", "나누", "구분", "각각", "대비", "비교", "설명")
    )
    plan_hit = any(tok in prompt for tok in ("계획", *_tok(prefs, "plan_columns")[:2]))
    exec_hit = any(tok in prompt for tok in ("실행", *_tok(prefs, "exec_columns")[:2]))
    return bool(has_up and has_down and has_split and plan_hit and exec_hit)


def pick_plan_vs_exec_columns(
    prompt: str,
    columns: list[str],
    *,
    profile_name: str | None = None,
) -> tuple[str, str] | None:
    """(left=exec/이후, right=plan/이전). 차이 = left − right."""
    del prompt
    prefs = _prefs(profile_name=profile_name)
    colset = {str(c) for c in columns}
    left = _first_present(colset, _tok(prefs, "exec_columns"))
    right = _first_present(colset, _tok(prefs, "plan_columns"))
    if not left or not right or left == right:
        return None
    return left, right


def apply_split_by_difference_prefs(
    prompt: str,
    data: dict[str, Any],
    columns: list[str],
    *,
    profile_name: str | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict) or not is_split_by_difference_prompt(
        prompt, profile_name=profile_name
    ):
        return data
    picked = pick_plan_vs_exec_columns(
        prompt, columns, profile_name=profile_name
    )
    if not picked:
        return data
    left, right = picked
    prefs = _prefs(profile_name=profile_name)
    colset = {str(c) for c in columns}
    labels = [c for c in _tok(prefs, "label_columns") if c in colset]
    diff_name = _str(prefs, "diff_name", "차이")
    label_name = _str(prefs, "split_label_name", "구분")
    out = dict(data)
    out["operation"] = "split_by_difference"
    out["left"] = left
    out["right"] = right
    out["value_columns"] = [left, right]
    out["diff_name"] = diff_name
    out["label_name"] = label_name
    out["output_columns"] = [*labels, right, left, diff_name, label_name]
    out["interpret"] = True
    out["criteria_note"] = (
        f"{diff_name} = {left} − {right}. 세부행을 증가/감소/동일으로 구분해 설명. "
        "상위 N으로 자르지 않음."
    )
    out.pop("steps", None)
    out.pop("limit", None)
    return out


def is_top_n_per_group_prompt(
    prompt: str,
    *,
    profile_name: str | None = None,
) -> bool:
    if not prompt:
        return False
    prefs = _prefs(profile_name=profile_name)
    group_tokens = _tok(prefs, "top_n_group_tokens")
    group_words = _tok(prefs, "top_n_group_words")
    pick_tokens = _tok(prefs, "top_n_pick_tokens")
    metric_tokens = _tok(prefs, "top_n_metric_tokens")
    if not group_tokens and not group_words:
        return False
    has_group = any(tok in prompt for tok in group_tokens) or (
        "별" in prompt and any(tok in prompt for tok in group_words)
    )
    has_pick = any(tok in prompt for tok in pick_tokens)
    has_metric = any(tok in prompt for tok in metric_tokens)
    return bool(has_group and has_pick and has_metric)


def pick_balance_column(
    prompt: str,
    columns: list[str],
    *,
    profile_name: str | None = None,
) -> str | None:
    prefs = _prefs(profile_name=profile_name)
    colset = {str(c) for c in columns}
    year_tokens = _tok(prefs, "current_year_tokens")
    wants_current = any(tok in prompt for tok in year_tokens) and ("잔액" in prompt)
    if wants_current:
        return _first_present(colset, _tok(prefs, "remaining_current"))
    return _first_present(colset, _tok(prefs, "remaining"))


def apply_top_n_per_group_prefs(
    prompt: str,
    data: dict[str, Any],
    columns: list[str],
    *,
    profile_name: str | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict) or not is_top_n_per_group_prompt(
        prompt, profile_name=profile_name
    ):
        return data
    prefs = _prefs(profile_name=profile_name)
    colset = {str(c) for c in columns}
    group_col = _first_present(colset, _tok(prefs, "group_columns"))
    if not group_col:
        return data

    value_col: str | None = None
    if "잔액" in prompt:
        value_col = pick_balance_column(
            prompt,
            columns,
            profile_name=profile_name,
        )
    if not value_col:
        candidate = str(data.get("value_column") or data.get("metric") or "").strip()
        if candidate in colset:
            value_col = candidate
        else:
            value_col = pick_balance_column(
                prompt,
                columns,
                profile_name=profile_name,
            ) or _first_present(colset, _tok(prefs, "top_n_fallback_metrics"))
    if not value_col:
        return data

    ascending = any(tok in prompt for tok in ("작", "낮", "최소", "하위")) and not any(
        tok in prompt for tok in ("큰", "높", "최대", "상위")
    )
    labels = [c for c in _tok(prefs, "label_columns") if c in colset]
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
    *,
    profile_name: str | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict) or not is_execution_efficiency_prompt(
        prompt, profile_name=profile_name
    ):
        return data
    picked = pick_execution_rate_columns(
        prompt, columns, profile_name=profile_name
    )
    if not picked:
        return data
    numerator, denominator = picked
    prefs = _prefs(profile_name=profile_name)
    rate_name = _str(prefs, "rate_name", "비율")
    out = dict(data)

    operation = str(out.get("operation") or "").strip()
    if operation in {"group_comparison", "compare_groups", "execution_rate_compare"}:
        out["numerator"] = numerator
        out["denominator"] = denominator
        out.setdefault("rate_name", rate_name)
        note = str(out.get("criteria_note") or "")
        preferred_note = (
            f"{rate_name} = {numerator} ÷ {denominator} "
            f"({'당년 기준' if asks_current_year_scope(prompt, profile_name=profile_name) else '합계 기준'})"
        )
        plan_cols = _tok(prefs, "plan_columns")
        if any(p in note for p in plan_cols) or "당년도" in note or not note:
            out["criteria_note"] = preferred_note

    steps = out.get("steps")
    if isinstance(steps, list):
        out["steps"] = _rewrite_steps_for_rate(steps, numerator, denominator, rate_name)

    return out


def is_group_efficiency_compare_prompt(
    prompt: str,
    *,
    profile_name: str | None = None,
) -> bool:
    if not prompt or not is_execution_efficiency_prompt(
        prompt, profile_name=profile_name
    ):
        return False
    has_pair = any(tok in prompt for tok in ("와", "과", "대비", "사이"))
    has_judge = any(
        tok in prompt
        for tok in (
            "어느",
            "더 효율",
            "더 높은",
            "비교",
            "차이",
            "해석",
            "설명",
        )
    )
    return bool(has_pair and has_judge)


def mentioned_category_labels(
    prompt: str,
    category_labels: list[str] | None,
) -> list[str]:
    """프롬프트에 등장하는 분류 라벨(긴 이름 우선)."""
    if not prompt or not category_labels:
        return []
    hits: list[str] = []
    for label in sorted(
        {str(x).strip() for x in category_labels if str(x).strip()},
        key=len,
        reverse=True,
    ):
        if label in prompt and label not in hits:
            hits.append(label)
    return hits


def apply_group_efficiency_compare_prefs(
    prompt: str,
    data: dict[str, Any],
    columns: list[str],
    *,
    category_labels: list[str] | None = None,
    profile_name: str | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict) or not is_group_efficiency_compare_prompt(
        prompt, profile_name=profile_name
    ):
        return data
    picked = pick_execution_rate_columns(
        prompt, columns, profile_name=profile_name
    )
    if not picked:
        return data
    numerator, denominator = picked
    prefs = _prefs(profile_name=profile_name)
    colset = {str(c) for c in columns}
    group_col = _first_present(colset, _tok(prefs, "group_columns"))
    if not group_col:
        return data

    groups = mentioned_category_labels(prompt, category_labels)
    if len(groups) < 2:
        existing = data.get("groups") or data.get("include_groups") or []
        if isinstance(existing, str):
            existing = [existing]
        groups = [str(g) for g in existing if str(g).strip()]
    if len(groups) < 2:
        return data

    rate_name = _str(prefs, "rate_name", "비율")
    out = dict(data)
    out["operation"] = "group_comparison"
    out["group_column"] = group_col
    out["groups"] = groups[:8]
    out["numerator"] = numerator
    out["denominator"] = denominator
    out["rate_name"] = rate_name
    out["prefer_subtotals"] = True
    out["interpret"] = True
    out["criteria_note"] = (
        f"{rate_name} = {numerator} ÷ {denominator}. "
        f"{group_col} 기준 {', '.join(groups[:8])} 비교."
    )
    out.pop("steps", None)
    return out


def apply_find_items_column_prefs(
    prompt: str,
    data: dict[str, Any],
    columns: list[str],
    *,
    profile_name: str | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict) or not is_carryover_no_current_exec_prompt(
        prompt, profile_name=profile_name
    ):
        return data
    prefs = _prefs(profile_name=profile_name)
    colset = {str(c) for c in columns}
    carry = _first_present(colset, _tok(prefs, "carryover_columns"))
    curr_exec = _first_present(colset, _tok(prefs, "current_exec_columns"))
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
    labels = [c for c in _tok(prefs, "label_columns") if c in colset]
    extras = [
        c
        for c in _tok(prefs, "find_related_metrics")
        if c in colset and c not in {carry, curr_exec}
    ][:2]
    out["output_columns"] = [*labels, carry, curr_exec, *extras]
    out["interpret"] = True
    out["criteria_note"] = (
        f"{carry} > 0 이고 {curr_exec} = 0인 세부 항목을 "
        "관련 열만 골라 큰 순으로 정렬했습니다."
    )
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
    rate_name: str = "비율",
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
            item.setdefault("name", rate_name)
        elif op == "aggregate":
            metrics = item.get("metrics") or []
            if isinstance(metrics, list):
                item["metrics"] = _ensure_metric_columns(metrics, [denominator, numerator])
        elif op == "compare_groups":
            metrics = item.get("metrics") or []
            if isinstance(metrics, list):
                name = rate_name
                for prev in steps:
                    if isinstance(prev, dict) and str(prev.get("op") or "") == "ratio_of_aggregates":
                        name = str(prev.get("name") or name)
                        break
                wanted = [denominator, numerator, name]
                if metrics and all(isinstance(m, str) for m in metrics):
                    item["metrics"] = wanted
                item["rate_columns"] = [name]
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
