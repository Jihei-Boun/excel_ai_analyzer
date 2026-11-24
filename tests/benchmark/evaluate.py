"""Level 1–4 evaluation helpers (semantic/structural, not exact plan JSON)."""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

import pandas as pd

from tests.benchmark.schema import BenchmarkCase, ExpectedSpec


def observe_route(meta: dict[str, Any] | None, *, system: bool = False) -> str:
    if system:
        return "system"
    meta = meta or {}
    # 라우터가 정해진 처리기(요약·구조·품질 …)로 보낸 질문
    if meta.get("request_kind") not in (None, "", "analysis"):
        return "system"
    agg = meta.get("aggregation") or {}
    op = str(agg.get("operation") or "")
    if op == "analysis_plan":
        return "analysis_plan"
    if op in {"legacy_simple_groupby_fallback", "groupby"}:
        return "legacy_fallback"
    if op in {"value_match", "list_seed"}:
        return "retrieval"
    if meta.get("source") == "pandasai" or op == "pandasai":
        return "pandasai"
    if meta.get("chart_path"):
        return "chart"
    return "unknown"


def plan_ops(plan_dict: dict[str, Any] | None) -> set[str]:
    if not plan_dict:
        return set()
    ops: set[str] = set()
    for step in plan_dict.get("steps") or []:
        if isinstance(step, dict):
            op = str(step.get("op") or step.get("operation") or "").strip()
            if op:
                ops.add(op)
    raw_op = str(plan_dict.get("operation") or "").strip()
    if raw_op:
        ops.add(raw_op)
    return ops


def plan_columns_used(plan_dict: dict[str, Any] | None) -> set[str]:
    """Collect column-like string leaves from a plan (best-effort)."""
    found: set[str] = set()

    def _walk(obj: Any, key_hint: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                _walk(v, str(k))
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, key_hint)
        elif isinstance(obj, str):
            hint = key_hint.lower()
            if any(
                tok in hint
                for tok in (
                    "column",
                    "group",
                    "metric",
                    "numerator",
                    "denominator",
                    "value",
                    "left",
                    "right",
                    "by",
                    "x_",
                    "y_",
                    "label",
                )
            ):
                if obj and not obj.startswith("_"):
                    found.add(obj)

    if not plan_dict:
        return found
    _walk(plan_dict)
    return found


def eval_routing(expected: ExpectedSpec, observed: str) -> tuple[bool | None, str]:
    if not expected.route:
        return None, "route not specified"
    want = expected.route
    aliases = {
        "analysis_plan": {"analysis_plan"},
        "system": {"system"},
        "retrieval": {"retrieval", "value_match", "list_seed"},
        "legacy_fallback": {"legacy_fallback", "groupby", "legacy_simple_groupby_fallback"},
        "pandasai": {"pandasai"},
        "fallback": {"legacy_fallback", "pandasai", "groupby", "legacy_simple_groupby_fallback"},
        "failure_safe": {"unknown", "pandasai", "legacy_fallback", "analysis_plan", "system"},
    }
    ok = observed in aliases.get(want, {want})
    if want == "failure_safe":
        # any non-crash route counts; caller marks crash separately
        ok = observed != "crash"
    return ok, f"expected={want} observed={observed}"


def eval_plan(expected: ExpectedSpec, plan_dict: dict[str, Any] | None) -> tuple[bool | None, str, str]:
    """Return (ok, detail, failure_category_hint)."""
    if not expected.required_operations and not expected.expected_columns and not expected.forbidden_columns:
        if expected.forbidden_operations:
            ops = plan_ops(plan_dict)
            bad = [op for op in expected.forbidden_operations if op in ops]
            if bad:
                return False, f"forbidden ops present: {bad}", "wrong_operation"
            return True, "forbidden ops absent", "none"
        return None, "plan checks not specified", "none"

    if plan_dict is None:
        return False, "no plan", "plan_generation_error"

    ops = plan_ops(plan_dict)
    missing = [op for op in expected.required_operations if op not in ops]
    # allow some aliases
    aliases = {
        "aggregate": {"aggregate", "ratio_of_aggregates", "compare_groups"},
        "filter_rows": {"filter_rows", "find_items"},
        "filter_vs_mean": {"filter_vs_mean", "rate_vs_mean"},
        "correlation": {"correlation"},
        "sort": {"sort"},
        "limit": {"limit"},
        "top_per_group": {"top_per_group", "top_n_per_group"},
        "compare_groups": {"compare_groups", "group_comparison"},
        "ratio_of_aggregates": {"ratio_of_aggregates", "group_comparison", "rate_vs_mean"},
    }
    still_missing = []
    for op in missing:
        alts = aliases.get(op, {op})
        if not alts.intersection(ops):
            still_missing.append(op)
    if still_missing:
        return False, f"missing ops {still_missing}; have {sorted(ops)}", "wrong_operation"

    forbidden = [op for op in expected.forbidden_operations if op in ops]
    if forbidden:
        return False, f"forbidden ops {forbidden}", "wrong_operation"

    cols = plan_columns_used(plan_dict)
    exp_cols = expected.expected_columns or {}
    for role, name in exp_cols.items():
        if role in {"group_by", "metric", "numerator", "denominator", "column", "x", "y"}:
            want = str(name)
            # role key itself may encode multiple names as list
            wants = name if isinstance(name, list) else [want]
            if not any(str(w) in cols or str(w) in str(plan_dict) for w in wants):
                return False, f"expected column {role}={wants} not found in plan", "wrong_column"

    for bad in expected.forbidden_columns:
        if bad in cols:
            return False, f"forbidden column {bad} used", "wrong_column"

    return True, f"ops={sorted(ops)}", "none"


def eval_execution(
    expected: ExpectedSpec,
    result_df: pd.DataFrame | None,
) -> tuple[bool | None, str, str]:
    if not expected.expected_result:
        if result_df is None:
            return None, "no result expectation", "none"
        return True, f"rows={len(result_df)}", "none"

    if result_df is None or (isinstance(result_df, pd.DataFrame) and result_df.empty and expected.expected_result):
        # empty may be valid for some filters; only fail if keys expected
        if expected.expected_result:
            return False, "empty/missing result", "wrong_result"
        return True, "empty ok", "none"

    tol = float(expected.result_tolerance)
    exp = expected.expected_result

    if any(k in exp for k in _CELL_KEYS):
        return _eval_cells(exp, result_df)

    # Pattern A: {label: value} mapped via first non-numeric col + first numeric col
    if all(not str(k).startswith("_") for k in exp.keys()) and not any(
        k in exp for k in ("row_count", "min_rows", "max_rows", "contains", "metric_values")
    ):
        label_cols = [
            c
            for c in result_df.columns
            if not pd.api.types.is_numeric_dtype(result_df[c])
        ]
        num_cols = [
            c
            for c in result_df.columns
            if pd.api.types.is_numeric_dtype(result_df[c])
            or pd.to_numeric(result_df[c], errors="coerce").notna().any()
        ]
        if label_cols and num_cols:
            label_col = label_cols[0]
            # prefer metric name from expected_columns
            metric = (expected.expected_columns or {}).get("metric")
            value_col = metric if metric in result_df.columns else num_cols[-1]
            mapping = {
                str(r[label_col]).strip(): float(pd.to_numeric(r[value_col], errors="coerce"))
                for _, r in result_df.iterrows()
            }
            for key, want in exp.items():
                if str(key) in {"row_count", "min_rows", "max_rows"}:
                    continue
                got = mapping.get(str(key))
                if got is None:
                    # try partial match
                    # 빈 라벨은 모든 문자열에 부분 일치하므로 제외한다.
                    hits = [v for k, v in mapping.items() if k and (str(key) in k or k in str(key))]
                    got = hits[0] if hits else None
                if got is None:
                    return False, f"missing label {key} in {mapping}", "wrong_result"
                if abs(float(got) - float(want)) > tol:
                    return False, f"{key}: got {got} want {want}", "wrong_result"
            return True, "label-value match", "none"

    if "row_count" in exp:
        if len(result_df) != int(exp["row_count"]):
            return False, f"row_count {len(result_df)} != {exp['row_count']}", "wrong_result"
    if "min_rows" in exp and len(result_df) < int(exp["min_rows"]):
        return False, f"min_rows fail {len(result_df)}", "wrong_result"
    if "max_rows" in exp and len(result_df) > int(exp["max_rows"]):
        return False, f"max_rows fail {len(result_df)}", "wrong_result"
    if "contains" in exp:
        blob = result_df.astype(str).to_csv(index=False)
        for token in exp["contains"]:
            if str(token) not in blob:
                return False, f"missing token {token}", "wrong_result"
    if "metric_values" in exp:
        for col, values in (exp["metric_values"] or {}).items():
            if col not in result_df.columns:
                return False, f"missing col {col}", "wrong_column"
            series = pd.to_numeric(result_df[col], errors="coerce")
            for want in values:
                if not any(abs(float(v) - float(want)) <= tol for v in series.dropna()):
                    return False, f"{col} missing value {want}", "wrong_result"

    return True, "result checks passed", "none"


_CELL_KEYS = (
    "cells", "ratio_cells", "pairs", "ratio_pairs", "labels", "absent_labels", "absent_cells",
    "rows", "row_order",
)


def _numbers_in(result_df: pd.DataFrame) -> list[float]:
    values: list[float] = []
    for col in result_df.columns:
        for v in pd.to_numeric(result_df[col], errors="coerce").dropna().tolist():
            values.append(float(v))
    return values


def _texts_in(result_df: pd.DataFrame) -> set[str]:
    texts = {str(c).strip() for c in result_df.columns}
    for col in result_df.columns:
        for v in result_df[col].tolist():
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            texts.add(str(v).strip())
    return texts


def _close(got: float, want: float, abs_tol: float = 0.01) -> bool:
    return abs(got - want) <= max(abs_tol, abs(want) * 1e-9)


def _value_matches(values: list[float], want: float, *, ratio: bool) -> bool:
    if ratio:
        return any(_close(v, want, 0.006) or _close(v, 100 * want, 0.06) for v in values)
    return any(_close(v, want) for v in values)


def _pair_present(result_df: pd.DataFrame, label: str, want: float, *, ratio: bool) -> bool:
    """라벨과 값이 같은 행에 있거나, 라벨 이름의 열에 값이 있으면 True (행·열 바꾼 표 포함)."""
    for _, row in result_df.iterrows():
        cells = [str(v).strip() for v in row.tolist()]
        if label in cells:
            numbers = pd.to_numeric(pd.Series(row.tolist()), errors="coerce").dropna().tolist()
            if _value_matches([float(v) for v in numbers], want, ratio=ratio):
                return True
    for col in result_df.columns:
        if str(col).strip() == label:
            numbers = pd.to_numeric(result_df[col], errors="coerce").dropna().tolist()
            if _value_matches([float(v) for v in numbers], want, ratio=ratio):
                return True
    return False


def _cell_label(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _label_alternatives(label: str) -> list[str]:
    return [part.strip() for part in label.split("|") if part.strip()]


def _vectors_for(result_df: pd.DataFrame, label: str) -> list[list[Any]]:
    """Rows that hold the label in a cell, and columns that hold it in the header or a cell."""
    names = set(_label_alternatives(label))
    vectors: list[list[Any]] = []
    for _, row in result_df.iterrows():
        cells = row.tolist()
        if names & {_cell_label(v) for v in cells}:
            vectors.append(cells)
    for col in result_df.columns:
        cells = result_df[col].tolist()
        if _cell_label(col) in names or names & {_cell_label(v) for v in cells}:
            vectors.append(cells)
    return vectors


def _atoms(value: Any) -> list[tuple[str, Any]]:
    """A cell split into the pieces a value can match: its text, each number in it, its date.

    A cell such as "한여울 · 56개월" gives the name and the number 56 separately, so a
    table that writes both in one cell is read the same as one that uses two cells.
    """
    if value is None or isinstance(value, bool) or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, (_dt.datetime, pd.Timestamp)):
        return [("date", value.date())]
    if isinstance(value, _dt.date):
        return [("date", value)]
    if isinstance(value, (int, float)):
        return [("num", float(value))]
    text = str(value).strip()
    atoms: list[tuple[str, Any]] = [("text", text)]
    atoms += [("num", float(m.replace(",", "").rstrip("%"))) for m in _NUM_RE.findall(text)]
    return atoms


def _date_in_text(want: _dt.date, text: str) -> bool:
    forms = (want.isoformat(), f"{want.month}/{want.day}", f"{want.month:02d}/{want.day:02d}",
             f"{want.month}월 {want.day}일", f"{want.month:02d}-{want.day:02d}")
    return any(form in text for form in forms)


def _spec_matches(spec: Any, atom: tuple[str, Any]) -> bool:
    kind, got = atom
    if isinstance(spec, dict) and "date" in spec:
        want = _dt.date.fromisoformat(str(spec["date"]))
        return (kind == "date" and got == want) or (kind == "text" and _date_in_text(want, got))
    if isinstance(spec, str):
        return kind == "text" and bool(spec) and spec in got
    if kind != "num":
        return False
    if isinstance(spec, dict) and "ratio" in spec:
        r, tol = float(spec["ratio"]), float(spec.get("tol", 0.00006))
        return abs(got - r) <= tol or abs(got - 100 * r) <= 100 * tol
    if isinstance(spec, dict) and "value" in spec:
        return abs(got - float(spec["value"])) <= float(spec.get("tol", 0.01))
    return _close(got, float(spec))


def _all_specs_fit(specs: list[Any], cells: list[Any]) -> bool:
    """Each expected value takes its own piece of a cell (bipartite matching, small tables only)."""
    atoms = [atom for cell in cells for atom in _atoms(cell)]
    owner: dict[int, int] = {}

    def assign(i: int, seen: set[int]) -> bool:
        for j, atom in enumerate(atoms):
            if j in seen or not _spec_matches(specs[i], atom):
                continue
            seen.add(j)
            if j not in owner or assign(owner[j], seen):
                owner[j] = i
                return True
        return False

    return all(assign(i, set()) for i in range(len(specs)))


def _row_present(result_df: pd.DataFrame, label: str, specs: list[Any]) -> bool:
    return any(_all_specs_fit(specs, cells) for cells in _vectors_for(result_df, label))


def _in_order(result_df: pd.DataFrame, labels: list[str]) -> bool:
    def first_positions(lines: list[list[Any]]) -> list[int] | None:
        found = []
        for label in labels:
            names = set(_label_alternatives(label))
            hit = next((i for i, cells in enumerate(lines) if names & {_cell_label(v) for v in cells}), None)
            if hit is None:
                return None
            found.append(hit)
        return found

    rows = [row.tolist() for _, row in result_df.iterrows()]
    cols = [[col, *result_df[col].tolist()] for col in result_df.columns]
    for lines in (rows, cols):
        pos = first_positions(lines)
        if pos is not None and len(set(pos)) == len(pos):
            return pos == sorted(pos)
    return False


def _eval_cells(exp: dict[str, Any], result_df: pd.DataFrame) -> tuple[bool, str, str]:
    """표 모양과 열 이름에 기대지 않는 검사. 행·열을 바꾼 표도 같은 기준으로 본다.

    - cells: 결과 표 어딘가에 이 숫자가 모두 있어야 한다 (소수 둘째 자리 반올림 허용).
    - ratio_cells: 비율 v가 v 또는 100·v(백분율)로 있어야 한다.
    - pairs / ratio_pairs: {라벨: 값}. 그 라벨이 있는 행(또는 그 이름의 열)에 그 값이 있어야 한다.
    - labels: 셀 값이나 열 이름에 이 글자가 정확히 있어야 한다.
    - absent_labels: 셀 값이나 열 이름에 이 글자가 들어가면 안 된다 (소계·총계 행 등).
    - absent_cells: 이 숫자가 있으면 안 된다 (잘못 고른 열이나 소계를 더한 값).
    - rows: {라벨: [값, ...]}. 그 라벨이 있는 한 행(또는 열)에 값이 모두, 서로 다른 칸에
      있어야 한다. 라벨은 "전체|합계"처럼 대안을 둘 수 있다. 값은 숫자, 글자(칸에 포함),
      {"ratio": r}, {"date": "YYYY-MM-DD"}, {"value": v, "tol": t}. 행·열 순서와 열 이름은 보지 않는다.
    - row_order: 이 라벨들이 이 순서로 나와야 한다 (행 방향이면 위에서 아래, 열 방향이면 왼쪽에서 오른쪽).
    - row_count / max_rows / min_rows: 행 수.
    """
    for label, wants in (exp.get("rows") or {}).items():
        if not _row_present(result_df, str(label), list(wants)):
            return False, f"{label}: values {wants} not together on one row/column", "wrong_result"
    order = [str(x) for x in exp.get("row_order") or []]
    if order and not _in_order(result_df, order):
        return False, f"order {order} not kept", "wrong_result"
    numbers = _numbers_in(result_df)
    texts = _texts_in(result_df)
    for want in exp.get("cells") or []:
        if not any(_close(v, float(want)) for v in numbers):
            return False, f"missing value {want}", "wrong_result"
    for want in exp.get("ratio_cells") or []:
        w = float(want)
        if not any(_close(v, w, 0.006) or _close(v, 100 * w, 0.06) for v in numbers):
            return False, f"missing ratio {want} (or {100 * w:.2f}%)", "wrong_result"
    for key, ratio in (("pairs", False), ("ratio_pairs", True)):
        for label, want in (exp.get(key) or {}).items():
            if not _pair_present(result_df, str(label), float(want), ratio=ratio):
                return False, f"{label}: value {want} not on its row/column", "wrong_result"
    for label in exp.get("labels") or []:
        if str(label) not in texts:
            return False, f"missing label {label}", "wrong_result"
    for label in exp.get("absent_labels") or []:
        hit = next((t for t in texts if str(label) in t), None)
        if hit is not None:
            return False, f"unexpected label {hit}", "wrong_result"
    for bad in exp.get("absent_cells") or []:
        if any(_close(v, float(bad)) for v in numbers):
            return False, f"unexpected value {bad}", "wrong_result"
    if "row_count" in exp and len(result_df) != int(exp["row_count"]):
        return False, f"row_count {len(result_df)} != {exp['row_count']}", "wrong_result"
    if "min_rows" in exp and len(result_df) < int(exp["min_rows"]):
        return False, f"min_rows fail {len(result_df)}", "wrong_result"
    if "max_rows" in exp and len(result_df) > int(exp["max_rows"]):
        return False, f"max_rows fail {len(result_df)}", "wrong_result"
    return True, "cell checks passed", "none"


def score_requirements(
    requirements: list[dict[str, Any]], result_df: pd.DataFrame | None
) -> dict[str, Any]:
    """요구사항마다 검사 하나를 돌려 몇 개를 지켰는지 센다.

    전부 맞아야 정답인 채점은 "8개 중 7개 맞음"과 "아무것도 못 함"을 구분하지 못한다.
    이 점수는 그 차이를 보려는 보조 지표다. 정답 판정은 expected_result가 한다.
    manual 요구사항은 세지 않고 사람이 판정하도록 남긴다.
    """
    items = []
    for req in requirements:
        text = str(req.get("text") or "")
        if req.get("manual"):
            items.append({"text": text, "ok": None, "detail": "manual"})
            continue
        if result_df is None:
            items.append({"text": text, "ok": False, "detail": "no answer table"})
            continue
        ok, detail, _ = _eval_cells(dict(req.get("check") or {}), result_df)
        items.append({"text": text, "ok": ok, "detail": detail})
    auto = [i for i in items if i["ok"] is not None]
    return {
        "met": sum(1 for i in auto if i["ok"]),
        "checked": len(auto),
        "manual": len(items) - len(auto),
        "items": items,
    }


_NUM_RE = re.compile(
    r"(?<![A-Za-z_])[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?%?"
)


def eval_interpretation_grounding(
    reply: str | None,
    result_df: pd.DataFrame | None,
) -> tuple[bool | None, str]:
    """Heuristic: numbers in reply should appear (approx) in result table."""
    if not reply or result_df is None:
        return None, "no interpretation to check"
    # Collect numbers from result
    allowed: set[str] = set()
    for col in result_df.columns:
        for val in result_df[col].tolist():
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            if isinstance(val, (int, float)):
                allowed.add(f"{val:.4g}")
                allowed.add(f"{float(val):.2f}")
                allowed.add(f"{float(val):.2%}")
                allowed.add(str(int(val)) if float(val).is_integer() else str(val))
            else:
                text = str(val)
                for m in _NUM_RE.findall(text):
                    allowed.add(m.replace(",", ""))

    invented = []
    for m in _NUM_RE.findall(reply):
        raw = m.replace(",", "").replace("%", "")
        try:
            num = float(raw)
        except ValueError:
            continue
        # allow small integers that are counts/ranks often restated
        if abs(num) < 20 and float(num).is_integer():
            continue
        ok = False
        for a in allowed:
            try:
                if abs(float(a.replace("%", "")) - num) <= max(1.0, abs(num) * 0.02):
                    ok = True
                    break
            except ValueError:
                if a in m or m in a:
                    ok = True
                    break
        if not ok:
            invented.append(m)
    if invented:
        return False, f"possible invented numbers: {invented[:5]}"
    return True, "no obvious invented numbers"


def classify_failure(
    *,
    case: BenchmarkCase,
    routing_ok: bool | None,
    plan_ok: bool | None,
    exec_ok: bool | None,
    interp_ok: bool | None,
    plan_hint: str,
    exec_hint: str,
    crashed: bool,
    route_observed: str,
) -> str:
    if crashed:
        return "crash"
    if case.expected.expect_safe_failure:
        return "safe_failure_ok"
    if routing_ok is False:
        return "routing_error"
    if plan_hint == "plan_validation_error" or "validation" in plan_hint:
        return "plan_validation_error"
    if plan_ok is False:
        return plan_hint if plan_hint in {
            "wrong_column",
            "wrong_operation",
            "plan_generation_error",
            "plan_validation_error",
        } else "wrong_operation"
    if exec_ok is False:
        return exec_hint if exec_hint in {
            "wrong_result",
            "wrong_column",
            "wrong_filter",
            "execution_error",
            "result_validation_error",
        } else "wrong_result"
    if interp_ok is False:
        return "interpreter_grounding_error"
    if route_observed in {"legacy_fallback", "pandasai"} and case.expected.route == "analysis_plan":
        return "fallback"
    return "none"
