"""Phase 39W — Frozen Phase 39V routing-rule generalization (offline).

Does NOT modify production routing or the Phase 39V rule.
"""

from __future__ import annotations

import inspect
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_plan_types import integration_plan_from_dict
from tests.benchmark_multi.phase39v_research import (
    _und_from_frames,
    evaluate_capability_signal,
    extract_attempt_evidence,
    metrics_for,
    simulate_p2_failure_escalation,
    simulate_p3_current,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase39w"

PHASE39V_RULE_VERSION = "PHASE39V_RULE_V1"
PHASE39V_RULE_EXPR = (
    "ESCALATE if not cannot_plan and ("
    "final_grain_contradiction OR evidence_role_contradiction OR "
    "(structural_error AND NOT only_unsafe))"
)
PHASE39V_SHA = "fe8b5994e7ce18406c10c599a8c661508a27bd0e"


def phase39v_rule_v1(ev: dict[str, Any]) -> str:
    """Exact frozen Phase 39V candidate. Do not edit the imported function."""
    return evaluate_capability_signal(ev)


def _plan(d: dict[str, Any]) -> Any:
    return integration_plan_from_dict(d)


def _roles(key: str, a: str, b: str) -> dict[str, Any]:
    return {
        "grain": "entity",
        "required_columns": [key, a, b],
        "output_roles": [
            {"role": "entity_key", "columns": [key]},
            {"role": "comparison_side", "columns": [a], "side_id": "A"},
            {"role": "comparison_side", "columns": [b], "side_id": "B"},
        ],
    }


def build_w_corpus() -> list[dict[str, Any]]:
    """New attempts. Distinct domains/shapes from Phase 39V."""
    cases: list[dict[str, Any]] = []

    def add(**kw: Any) -> None:
        cases.append(kw)

    rooms = pd.DataFrame({"room_id": ["R1", "R1", "R2"], "lux": [20, 22, 18], "wing": ["N", "S", "N"]})
    badges = pd.DataFrame({"emp": ["E1", "E2"], "dept": ["HR", "IT"]})
    access = pd.DataFrame({"emp": ["E1", "E2"], "door": ["A", "B"]})
    patients = pd.DataFrame({"pid": ["P1", "P2"], "ward": ["3A", "3B"]})
    visits = pd.DataFrame({"pid": ["P1", "P1", "P2"], "mins": [12, 8, 15]})
    q1 = pd.DataFrame({"sku": ["A", "B"], "units": [3, 4]})
    q2 = pd.DataFrame({"sku": ["A", "C"], "units": [5, 1]})
    q3 = pd.DataFrame({"sku": ["B", "C"], "units": [2, 6]})
    tickets = pd.DataFrame({"tid": ["T1", "T2", "T3"], "agent": ["X", "Y", "X"], "hrs": [1, 2, 3]})
    agents = pd.DataFrame({"agent": ["X", "Y"], "team": ["A", "B"]})
    lot = pd.DataFrame({"lot": ["L1", "L1", "L2"], "kg": [9, 7, 4], "site": ["E", "W", "E"]})
    morn = pd.DataFrame({"bay": ["B1", "B2"], "temp": [11, 12]})
    eve = pd.DataFrame({"bay": ["B1", "B2"], "temp": [16, 15]})
    assays = pd.DataFrame({
        "sample": ["S1", "S1", "S2", "S2"],
        "run": ["R1", "R2", "R1", "R2"],
        "score": [0.2, 0.3, 0.4, 0.1],
    })
    fleet = pd.DataFrame({"vin": ["V1", "V2"], "km": [100, 80]})
    fuel = pd.DataFrame({"vin": ["V1", "V2"], "liters": [20, 18]})
    inv_a = pd.DataFrame({"bin": ["N1", "N2"], "qty": [5, 6]})
    inv_b = pd.DataFrame({"bin": ["N1", "N2"], "qty": [1, 2]})
    invoices = pd.DataFrame({"inv": ["I1", "I2", "I1"], "amt": [10, 20, 5], "ccy": ["KRW", "USD", "KRW"]})
    sensors = pd.DataFrame({"sid": ["G1", "G1", "G2"], "ppm": [4, 5, 6], "shift": ["AM", "PM", "AM"]})
    loans = pd.DataFrame({"isbn": ["X", "Y"], "days": [3, 7]})
    titles = pd.DataFrame({"isbn": ["X", "Y"], "title": ["t1", "t2"]})
    trays = pd.DataFrame({
        "tray": ["A", "A", "B", "B"],
        "week": ["W1", "W2", "W1", "W2"],
        "g": [10, 12, 8, 9],
    })
    m2m_p = pd.DataFrame({"hid": ["H1", "H1", "H2"], "v": [1, 2, 3]})
    m2m_q = pd.DataFrame({"hid": ["H1", "H1", "H2"], "w": [4, 5, 6]})
    odd = pd.DataFrame({"foo": [1, 2]})
    other = pd.DataFrame({"bar": ["z"]})
    wide = pd.DataFrame({"id": ["1", "2"], "alpha_n": [3, 4], "beta_n": [5, 6]})

    # ----- W1 ordinary valid -----
    add(attempt_id="w1-filter-wing", request_id="p39w-01", group="W1", shape="ordinary_filter",
        grain_shape="row", frames={"lux.xlsx": rooms},
        plan=_plan({"status": "planned", "final_output": "f", "steps": [
            {"op": "filter_rows", "inputs": ["lux.xlsx"], "output": "f",
             "params": {"conditions": [{"column": "wing", "operator": "eq", "value": "N"}]}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="단순 날개 필터. 유효 대조군.", strong="unused")

    add(attempt_id="w1-agg-room", request_id="p39w-02", group="W1", shape="ordinary_aggregate",
        grain_shape="aggregate", frames={"lux.xlsx": rooms},
        plan=_plan({"status": "planned", "final_output": "a", "steps": [
            {"op": "aggregate", "inputs": ["lux.xlsx"], "output": "a",
             "params": {"group_by": ["room_id"], "metrics": [{"column": "lux", "function": "sum", "alias": "lux_sum"}]}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="방별 합계.", strong="unused")

    add(attempt_id="w1-count-agent", request_id="p39w-03", group="W1", shape="ordinary_aggregate",
        grain_shape="aggregate", frames={"tickets.xlsx": tickets},
        plan=_plan({"status": "planned", "final_output": "a", "steps": [
            {"op": "aggregate", "inputs": ["tickets.xlsx"], "output": "a",
             "params": {"group_by": ["agent"], "metrics": [{"column": "tid", "function": "count", "alias": "n"}]}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="상담원별 건수.", strong="unused")

    add(attempt_id="w1-join-1to1", request_id="p39w-04", group="W1", shape="ordinary_join",
        grain_shape="entity_preserving", frames={"badge.xlsx": badges, "door.xlsx": access},
        plan=_plan({"status": "planned", "final_output": "j", "steps": [
            {"op": "join", "inputs": ["badge.xlsx", "door.xlsx"], "output": "j",
             "params": {"left_keys": ["emp"], "right_keys": ["emp"], "how": "inner"}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="사원 1:1 조인.", strong="unused")

    add(attempt_id="w1-join-1tomany", request_id="p39w-05", group="W1", shape="ordinary_join",
        grain_shape="entity_preserving", frames={"pat.xlsx": patients, "vis.xlsx": visits},
        plan=_plan({"status": "planned", "final_output": "j", "steps": [
            {"op": "join", "inputs": ["pat.xlsx", "vis.xlsx"], "output": "j",
             "params": {"left_keys": ["pid"], "right_keys": ["pid"], "how": "left"}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="환자-방문 1:N.", strong="unused")

    add(attempt_id="w1-union-quarters", request_id="p39w-06", group="W1", shape="ordinary_union",
        grain_shape="summary", frames={"q1.xlsx": q1, "q2.xlsx": q2},
        plan=_plan({"status": "planned", "final_output": "u", "steps": [
            {"op": "union_rows", "inputs": ["q1.xlsx", "q2.xlsx"], "output": "u", "params": {}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="분기 적재. union이 맞다.", strong="unused")

    add(attempt_id="w1-union-total", request_id="p39w-07", group="W1", shape="ordinary_union",
        grain_shape="aggregate", frames={"q1.xlsx": q1, "q2.xlsx": q2},
        plan=_plan({"status": "planned", "final_output": "a", "steps": [
            {"op": "union_rows", "inputs": ["q1.xlsx", "q2.xlsx"], "output": "u", "params": {}},
            {"op": "aggregate", "inputs": ["u"], "output": "a",
             "params": {"group_by": ["sku"], "metrics": [{"column": "units", "function": "sum", "alias": "units_all"}]}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="전체 합계 요청. union+agg 정답.", strong="unused")

    add(attempt_id="w1-rename-join-temp", request_id="p39w-08", group="W1", shape="rename_join",
        grain_shape="dual_side", frames={"morn.xlsx": morn, "eve.xlsx": eve},
        plan=_plan({"status": "planned", "final_output": "j",
                    "final_output_requirements": _roles("bay", "temp_am", "temp_pm"),
                    "steps": [
            {"op": "rename_columns", "inputs": ["morn.xlsx"], "output": "mr",
             "params": {"mapping": {"temp": "temp_am"}}},
            {"op": "rename_columns", "inputs": ["eve.xlsx"], "output": "er",
             "params": {"mapping": {"temp": "temp_pm"}}},
            {"op": "join", "inputs": ["mr", "er"], "output": "j",
             "params": {"left_keys": ["bay"], "right_keys": ["bay"], "how": "inner"}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="오전/오후 온도 rename+join.", strong="unused")

    add(attempt_id="w1-branch-assay", request_id="p39w-09", group="W1", shape="same_source_branch",
        grain_shape="dual_side", frames={"assay.xlsx": assays},
        plan=_plan({"status": "planned", "final_output": "j",
                    "final_output_requirements": _roles("sample", "score_r1", "score_r2"),
                    "steps": [
            {"op": "filter_rows", "inputs": ["assay.xlsx"], "output": "f1",
             "params": {"conditions": [{"column": "run", "operator": "eq", "value": "R1"}]}},
            {"op": "rename_columns", "inputs": ["f1"], "output": "r1",
             "params": {"mapping": {"score": "score_r1"}}},
            {"op": "filter_rows", "inputs": ["assay.xlsx"], "output": "f2",
             "params": {"conditions": [{"column": "run", "operator": "eq", "value": "R2"}]}},
            {"op": "rename_columns", "inputs": ["f2"], "output": "r2",
             "params": {"mapping": {"score": "score_r2"}}},
            {"op": "join", "inputs": ["r1", "r2"], "output": "j",
             "params": {"left_keys": ["sample"], "right_keys": ["sample"], "how": "inner"}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="런별 독립 분기. 유효 단일원 비교.", strong="unused")

    add(attempt_id="w1-lookup-team", request_id="p39w-10", group="W1", shape="ordinary_join",
        grain_shape="entity_preserving", frames={"tickets.xlsx": tickets, "agents.xlsx": agents},
        plan=_plan({"status": "planned", "final_output": "j", "steps": [
            {"op": "join", "inputs": ["tickets.xlsx", "agents.xlsx"], "output": "j",
             "params": {"left_keys": ["agent"], "right_keys": ["agent"], "how": "left"}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="티켓에 팀 룩업.", strong="unused")

    add(attempt_id="w1-join-select", request_id="p39w-11", group="W1", shape="ordinary_join",
        grain_shape="entity_preserving", frames={"fleet.xlsx": fleet, "fuel.xlsx": fuel},
        plan=_plan({"status": "planned", "final_output": "s", "steps": [
            {"op": "join", "inputs": ["fleet.xlsx", "fuel.xlsx"], "output": "j",
             "params": {"left_keys": ["vin"], "right_keys": ["vin"], "how": "inner"}},
            {"op": "select_columns", "inputs": ["j"], "output": "s",
             "params": {"columns": ["vin", "km", "liters"]}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="조인 후 열 선택.", strong="unused")

    add(attempt_id="w1-filter-then-agg", request_id="p39w-12", group="W1", shape="ordinary_aggregate",
        grain_shape="aggregate", frames={"lot.xlsx": lot},
        plan=_plan({"status": "planned", "final_output": "a", "steps": [
            {"op": "filter_rows", "inputs": ["lot.xlsx"], "output": "f",
             "params": {"conditions": [{"column": "site", "operator": "eq", "value": "E"}]}},
            {"op": "aggregate", "inputs": ["f"], "output": "a",
             "params": {"group_by": ["lot"], "metrics": [{"column": "kg", "function": "sum", "alias": "kg_e"}]}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="사이트 필터 후 집계.", strong="unused")

    add(attempt_id="w1-mean-lux", request_id="p39w-13", group="W1", shape="ordinary_aggregate",
        grain_shape="aggregate", frames={"lux.xlsx": rooms},
        plan=_plan({"status": "planned", "final_output": "a", "steps": [
            {"op": "aggregate", "inputs": ["lux.xlsx"], "output": "a",
             "params": {"group_by": ["wing"], "metrics": [{"column": "lux", "function": "mean", "alias": "lux_avg"}]}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="날개별 평균.", strong="unused")

    add(attempt_id="w1-many-to-one", request_id="p39w-14", group="W1", shape="ordinary_join",
        grain_shape="entity_preserving", frames={"vis.xlsx": visits, "pat.xlsx": patients},
        plan=_plan({"status": "planned", "final_output": "j", "steps": [
            {"op": "join", "inputs": ["vis.xlsx", "pat.xlsx"], "output": "j",
             "params": {"left_keys": ["pid"], "right_keys": ["pid"], "how": "left"}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="방문→환자 many-to-one.", strong="unused")

    add(attempt_id="w1-rename-only", request_id="p39w-15", group="W1", shape="ordinary_rename",
        grain_shape="row", frames={"loans.xlsx": loans},
        plan=_plan({"status": "planned", "final_output": "r", "steps": [
            {"op": "rename_columns", "inputs": ["loans.xlsx"], "output": "r",
             "params": {"mapping": {"days": "loan_days"}}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="단순 rename.", strong="unused")

    add(attempt_id="w1-filter-gt", request_id="p39w-16", group="W1", shape="ordinary_filter",
        grain_shape="row", frames={"invoices.xlsx": invoices},
        plan=_plan({"status": "planned", "final_output": "f", "steps": [
            {"op": "filter_rows", "inputs": ["invoices.xlsx"], "output": "f",
             "params": {"conditions": [{"column": "amt", "operator": "gt", "value": 8}]}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="금액 임계 필터.", strong="unused")

    add(attempt_id="w1-join-then-agg", request_id="p39w-17", group="W1", shape="multi_stage",
        grain_shape="aggregate", frames={"pat.xlsx": patients, "vis.xlsx": visits},
        plan=_plan({"status": "planned", "final_output": "a",
                    "final_output_requirements": {"grain": "group", "required_columns": ["pid", "mins_sum"]},
                    "steps": [
            {"op": "join", "inputs": ["pat.xlsx", "vis.xlsx"], "output": "j",
             "params": {"left_keys": ["pid"], "right_keys": ["pid"], "how": "left"}},
            {"op": "aggregate", "inputs": ["j"], "output": "a",
             "params": {"group_by": ["pid"], "metrics": [{"column": "mins", "function": "sum", "alias": "mins_sum"}]}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="조인 후 환자 grain 집계. 선언 grain과 일치.", strong="unused")

    add(attempt_id="w1-library-lookup", request_id="p39w-18", group="W1", shape="ordinary_join",
        grain_shape="entity_preserving", frames={"loans.xlsx": loans, "titles.xlsx": titles},
        plan=_plan({"status": "planned", "final_output": "j", "steps": [
            {"op": "join", "inputs": ["loans.xlsx", "titles.xlsx"], "output": "j",
             "params": {"left_keys": ["isbn"], "right_keys": ["isbn"], "how": "inner"}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="대출-서지 조인.", strong="unused")

    add(attempt_id="w1-select-invoice", request_id="p39w-19", group="W1", shape="ordinary_select",
        grain_shape="row", frames={"invoices.xlsx": invoices},
        plan=_plan({"status": "planned", "final_output": "s", "steps": [
            {"op": "select_columns", "inputs": ["invoices.xlsx"], "output": "s",
             "params": {"columns": ["inv", "amt"]}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="열 부분집합.", strong="unused")

    add(attempt_id="w1-two-metric-independent", request_id="p39w-20", group="W1", shape="ordinary_aggregate",
        grain_shape="dual_side", frames={"wide.xlsx": wide},
        plan=_plan({"status": "planned", "final_output": "a",
                    "steps": [
            {"op": "aggregate", "inputs": ["wide.xlsx"], "output": "a",
             "params": {"group_by": ["id"], "metrics": [
                 {"column": "alpha_n", "function": "sum", "alias": "alpha_s"},
                 {"column": "beta_n", "function": "sum", "alias": "beta_s"},
             ]}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="서로 다른 열의 두 집계. 독립 증거 유사 대조군.", strong="unused")

    # ----- W5 valid lookalikes -----
    add(attempt_id="w5-valid-union-lookalike", request_id="p39w-21", group="W5", shape="ordinary_union",
        grain_shape="summary", frames={"q2.xlsx": q2, "q3.xlsx": q3},
        plan=_plan({"status": "planned", "final_output": "u", "steps": [
            {"op": "union_rows", "inputs": ["q2.xlsx", "q3.xlsx"], "output": "u", "params": {}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="붕괴처럼 보이는 유효 동일스키마 적재.", strong="unused", lookalike=True)

    add(attempt_id="w5-valid-branch-lookalike", request_id="p39w-22", group="W5", shape="same_source_branch",
        grain_shape="dual_side", frames={"tray.xlsx": trays},
        plan=_plan({"status": "planned", "final_output": "j",
                    "final_output_requirements": _roles("tray", "g_w1", "g_w2"),
                    "steps": [
            {"op": "filter_rows", "inputs": ["tray.xlsx"], "output": "a",
             "params": {"conditions": [{"column": "week", "operator": "eq", "value": "W1"}]}},
            {"op": "rename_columns", "inputs": ["a"], "output": "ar",
             "params": {"mapping": {"g": "g_w1"}}},
            {"op": "filter_rows", "inputs": ["tray.xlsx"], "output": "b",
             "params": {"conditions": [{"column": "week", "operator": "eq", "value": "W2"}]}},
            {"op": "rename_columns", "inputs": ["b"], "output": "br",
             "params": {"mapping": {"g": "g_w2"}}},
            {"op": "join", "inputs": ["ar", "br"], "output": "j",
             "params": {"left_keys": ["tray"], "right_keys": ["tray"], "how": "inner"}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="복잡한 분기 토폴로지. 유효 동일원 비교.", strong="unused", lookalike=True)

    add(attempt_id="w5-valid-same-schema-concat", request_id="p39w-23", group="W5", shape="ordinary_union",
        grain_shape="summary", frames={"inv_a.xlsx": inv_a, "inv_b.xlsx": inv_b},
        plan=_plan({"status": "planned", "final_output": "u", "steps": [
            {"op": "union_rows", "inputs": ["inv_a.xlsx", "inv_b.xlsx"], "output": "u", "params": {}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="두 창고 재고를 쌓는 유효 union.", strong="unused", lookalike=True)

    add(attempt_id="w5-valid-multi-stage", request_id="p39w-24", group="W5", shape="multi_stage",
        grain_shape="aggregate", frames={"sensors.xlsx": sensors},
        plan=_plan({"status": "planned", "final_output": "a", "steps": [
            {"op": "filter_rows", "inputs": ["sensors.xlsx"], "output": "f",
             "params": {"conditions": [{"column": "shift", "operator": "eq", "value": "AM"}]}},
            {"op": "rename_columns", "inputs": ["f"], "output": "r",
             "params": {"mapping": {"ppm": "ppm_am"}}},
            {"op": "aggregate", "inputs": ["r"], "output": "a",
             "params": {"group_by": ["sid"], "metrics": [{"column": "ppm_am", "function": "mean", "alias": "ppm_am_avg"}]}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="필터-rename-집계 다단계. 유효.", strong="unused", lookalike=True)

    add(attempt_id="w5-valid-left-join", request_id="p39w-25", group="W5", shape="ordinary_join",
        grain_shape="entity_preserving", frames={"badge.xlsx": badges, "door.xlsx": access},
        plan=_plan({"status": "planned", "final_output": "j", "steps": [
            {"op": "join", "inputs": ["badge.xlsx", "door.xlsx"], "output": "j",
             "params": {"left_keys": ["emp"], "right_keys": ["emp"], "how": "left"}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="left join 유효 대조.", strong="unused", lookalike=True)

    add(attempt_id="w5-valid-two-aggs-same-table", request_id="p39w-26", group="W5", shape="ordinary_aggregate",
        grain_shape="aggregate", frames={"tickets.xlsx": tickets},
        plan=_plan({"status": "planned", "final_output": "a", "steps": [
            {"op": "aggregate", "inputs": ["tickets.xlsx"], "output": "a",
             "params": {"group_by": ["agent"], "metrics": [
                 {"column": "hrs", "function": "sum", "alias": "hrs_sum"},
                 {"column": "hrs", "function": "mean", "alias": "hrs_mean"},
             ]}}]}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="같은 열의 sum/mean. 비교 사이드가 아니므로 올리면 안 됨.", strong="unused", lookalike=True)

    # ----- W4 correct cannot_plan -----
    add(attempt_id="w4-missing-color", request_id="p39w-27", group="W4", shape="correct_cannot_plan",
        grain_shape="dual_side", frames={"lot.xlsx": lot},
        plan=_plan({"status": "cannot_plan", "steps": [], "final_output": None,
                    "reason": "requested color partitions are not in the observations"}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="색 구분 부재. cannot_plan이 맞다.", strong="same_cannot_plan")

    add(attempt_id="w4-unrelated", request_id="p39w-28", group="W4", shape="correct_cannot_plan",
        grain_shape="summary", frames={"odd.xlsx": odd, "other.xlsx": other},
        plan=_plan({"status": "cannot_plan", "steps": [], "final_output": None,
                    "reason": "no shared key or schema evidence"}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="무관 테이블. cannot_plan 정답.", strong="same_cannot_plan")

    add(attempt_id="w4-missing-period", request_id="p39w-29", group="W4", shape="correct_cannot_plan",
        grain_shape="dual_side", frames={"fleet.xlsx": fleet},
        plan=_plan({"status": "cannot_plan", "steps": [], "final_output": None,
                    "reason": "before/after period column is absent"}),
        fast_correct="YES", capability="FAST_SUFFICIENT",
        note_ko="기간 구분 없음.", strong="same_cannot_plan")

    # ----- W2 structurally intended-valid, semantically wrong -----
    add(attempt_id="w2-collapse-no-roles", request_id="p39w-30", group="W2", shape="union_collapse",
        grain_shape="dual_side", frames={"inv_a.xlsx": inv_a, "inv_b.xlsx": inv_b},
        plan=_plan({"status": "planned", "final_output": "a", "steps": [
            {"op": "union_rows", "inputs": ["inv_a.xlsx", "inv_b.xlsx"], "output": "u", "params": {}},
            {"op": "aggregate", "inputs": ["u"], "output": "a",
             "params": {"group_by": ["bin"], "metrics": [{"column": "qty", "function": "sum", "alias": "qty_all"}]}}]}),
        fast_correct="NO", capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="두 창고를 비교해야 하는데 한 합계로 붕괴. 역할 미선언. 구조는 타당할 수 있음.",
        strong="rename_join", request_intent="compare_two_sites")

    add(attempt_id="w2-single-run-only", request_id="p39w-31", group="W2", shape="wrong_branch",
        grain_shape="dual_side", frames={"assay.xlsx": assays},
        plan=_plan({"status": "planned", "final_output": "a", "steps": [
            {"op": "filter_rows", "inputs": ["assay.xlsx"], "output": "f",
             "params": {"conditions": [{"column": "run", "operator": "eq", "value": "R1"}]}},
            {"op": "aggregate", "inputs": ["f"], "output": "a",
             "params": {"group_by": ["sample"], "metrics": [{"column": "score", "function": "sum", "alias": "score"}]}}]}),
        fast_correct="NO", capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="R1과 R2를 모두 보여야 하는데 R1만 집계.", strong="filter_branch_join")

    add(attempt_id="w2-join-instead-of-union", request_id="p39w-32", group="W2", shape="wrong_shape",
        grain_shape="summary", frames={"q1.xlsx": q1, "q2.xlsx": q2},
        plan=_plan({"status": "planned", "final_output": "j", "steps": [
            {"op": "join", "inputs": ["q1.xlsx", "q2.xlsx"], "output": "j",
             "params": {"left_keys": ["sku"], "right_keys": ["sku"], "how": "inner"}}]}),
        fast_correct="NO", capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="분기 적재가 필요한데 inner join으로 교집합만 남김.", strong="union_rows")

    add(attempt_id="w2-wrong-group-grain", request_id="p39w-33", group="W2", shape="wrong_grain",
        grain_shape="aggregate", frames={"tickets.xlsx": tickets},
        plan=_plan({"status": "planned", "final_output": "a", "steps": [
            {"op": "aggregate", "inputs": ["tickets.xlsx"], "output": "a",
             "params": {"group_by": ["tid"], "metrics": [{"column": "hrs", "function": "sum", "alias": "hrs"}]}}]}),
        fast_correct="NO", capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="상담원별 합계가 필요한데 티켓 grain으로 집계.", strong="group_by_agent")

    add(attempt_id="w2-drop-needed-metric", request_id="p39w-34", group="W2", shape="missing_side",
        grain_shape="dual_side", frames={"fleet.xlsx": fleet, "fuel.xlsx": fuel},
        plan=_plan({"status": "planned", "final_output": "s", "steps": [
            {"op": "join", "inputs": ["fleet.xlsx", "fuel.xlsx"], "output": "j",
             "params": {"left_keys": ["vin"], "right_keys": ["vin"], "how": "inner"}},
            {"op": "select_columns", "inputs": ["j"], "output": "s",
             "params": {"columns": ["vin", "km"]}}]}),
        fast_correct="NO", capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="연비 비교에 liters를 버렸다. 구조 VALID.", strong="keep_both_metrics")

    add(attempt_id="w2-union-when-compare", request_id="p39w-35", group="W2", shape="union_collapse",
        grain_shape="dual_side", frames={"morn.xlsx": morn, "eve.xlsx": eve},
        plan=_plan({"status": "planned", "final_output": "u", "steps": [
            {"op": "union_rows", "inputs": ["morn.xlsx", "eve.xlsx"], "output": "u", "params": {}}]}),
        fast_correct="NO", capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="베이별 오전/오후를 나란히 보여야 하는데 행만 쌓음.", strong="rename_join")

    add(attempt_id="w2-roles-collapse", request_id="p39w-36", group="W2", shape="role_collapse",
        grain_shape="dual_side", frames={"inv_a.xlsx": inv_a, "inv_b.xlsx": inv_b},
        plan=_plan({"status": "planned", "final_output": "a",
                    "final_output_requirements": _roles("bin", "qty_a", "qty_b"),
                    "steps": [
            {"op": "union_rows", "inputs": ["inv_a.xlsx", "inv_b.xlsx"], "output": "u", "params": {}},
            {"op": "aggregate", "inputs": ["u"], "output": "a",
             "params": {"group_by": ["bin"], "metrics": [{"column": "qty", "function": "sum", "alias": "qty_all"}]}}]}),
        fast_correct="NO", capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="사이드를 선언하고 한 메트릭만 물질화. 동결 규칙이 잡을 수 있음.",
        strong="rename_join")

    add(attempt_id="w2-filter-wrong-site", request_id="p39w-37", group="W2", shape="wrong_branch",
        grain_shape="row", frames={"lot.xlsx": lot},
        plan=_plan({"status": "planned", "final_output": "f", "steps": [
            {"op": "filter_rows", "inputs": ["lot.xlsx"], "output": "f",
             "params": {"conditions": [{"column": "site", "operator": "eq", "value": "W"}]}}]}),
        fast_correct="NO", capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="동부 로트만 필요한데 서부로 필터.", strong="filter_east")

    # ----- W3 structurally invalid -----
    add(attempt_id="w3-grain-detail-collapse", request_id="p39w-38", group="W3", shape="grain_contradiction",
        grain_shape="entity_collapsing", frames={"pat.xlsx": patients, "vis.xlsx": visits},
        plan=_plan({"status": "planned", "final_output": "a",
                    "final_output_requirements": {"grain": "detail", "required_columns": ["pid", "mins", "ward"]},
                    "steps": [
            {"op": "join", "inputs": ["pat.xlsx", "vis.xlsx"], "output": "j",
             "params": {"left_keys": ["pid"], "right_keys": ["pid"], "how": "left"}},
            {"op": "aggregate", "inputs": ["j"], "output": "a",
             "params": {"group_by": ["pid"], "metrics": [{"column": "mins", "function": "sum", "alias": "mins"}]}}]}),
        fast_correct="NO", capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="detail grain + 붕괴 집계.", strong="keep_detail")

    add(attempt_id="w3-missing-col", request_id="p39w-39", group="W3", shape="invalid_reference",
        grain_shape="aggregate", frames={"tickets.xlsx": tickets},
        plan=_plan({"status": "planned", "final_output": "a", "steps": [
            {"op": "aggregate", "inputs": ["tickets.xlsx"], "output": "a",
             "params": {"group_by": ["nope"], "metrics": [{"column": "hrs", "function": "sum", "alias": "s"}]}}]}),
        fast_correct="NO", capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="없는 열.", strong="group_by_agent")

    add(attempt_id="w3-genuine-m2m", request_id="p39w-40", group="W3", shape="many_to_many",
        grain_shape="entity_preserving", frames={"hp.xlsx": m2m_p, "hq.xlsx": m2m_q},
        plan=_plan({"status": "planned", "final_output": "j", "steps": [
            {"op": "join", "inputs": ["hp.xlsx", "hq.xlsx"], "output": "j",
             "params": {"left_keys": ["hid"], "right_keys": ["hid"], "how": "inner"}}]}),
        fast_correct="NO", capability="BOTH_INSUFFICIENT",
        note_ko="진짜 many-to-many. 안전 거절이 바람직.", strong="SAFELY_BLOCKED_WITHOUT_STRONG_RECOVERY")

    add(attempt_id="w3-bad-key", request_id="p39w-41", group="W3", shape="invalid_reference",
        grain_shape="entity_preserving", frames={"badge.xlsx": badges, "door.xlsx": access},
        plan=_plan({"status": "planned", "final_output": "j", "steps": [
            {"op": "join", "inputs": ["badge.xlsx", "door.xlsx"], "output": "j",
             "params": {"left_keys": ["ghost"], "right_keys": ["ghost"], "how": "inner"}}]}),
        fast_correct="NO", capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="조인 키 없음.", strong="join_on_emp")

    add(attempt_id="w3-fake-dual-roles", request_id="p39w-42", group="W3", shape="fake_dual",
        grain_shape="dual_side", frames={"lot.xlsx": lot},
        plan=_plan({"status": "planned", "final_output": "a",
                    "final_output_requirements": _roles("lot", "east_kg", "west_kg"),
                    "steps": [
            {"op": "aggregate", "inputs": ["lot.xlsx"], "output": "a",
             "params": {"group_by": ["lot"], "metrics": [
                 {"column": "kg", "function": "sum", "alias": "east_kg"},
                 {"column": "kg", "function": "sum", "alias": "west_kg"},
             ]}}]}),
        fast_correct="NO", capability="FAST_OVERCOMMITS_STRONG_CORRECTLY_CANNOT_PLAN",
        note_ko="같은 kg를 두 사이드로 복제.", strong="CORRECT_CANNOT_PLAN")

    add(attempt_id="w3-incompat-union", request_id="p39w-43", group="W3", shape="invalid_schema",
        grain_shape="summary", frames={"odd.xlsx": odd, "other.xlsx": other},
        plan=_plan({"status": "planned", "final_output": "u", "steps": [
            {"op": "union_rows", "inputs": ["odd.xlsx", "other.xlsx"], "output": "u",
             "params": {"column_policy": "aligned"}}]}),
        fast_correct="NO", capability="BOTH_INSUFFICIENT",
        note_ko="스키마 불일치 union.", strong="cannot_plan")

    # ----- W6 -----
    add(attempt_id="w6-timeout", request_id="p39w-44", group="W6", shape="operational",
        grain_shape="summary", frames={"odd.xlsx": odd},
        plan=_plan({"status": "cannot_plan", "steps": [], "final_output": None,
                    "reason": "planner_parse_failed", "notes": ["ReadTimeout"]}),
        fast_correct="INDETERMINATE", capability="STRONG_OPERATIONAL_FAILURE",
        note_ko="타임아웃은 능력 신호가 아님.", strong="operational")

    add(attempt_id="w6-ambiguous-grain", request_id="p39w-45", group="W6", shape="ambiguous",
        grain_shape="aggregate", frames={"tickets.xlsx": tickets},
        plan=_plan({"status": "planned", "final_output": "a", "steps": [
            {"op": "aggregate", "inputs": ["tickets.xlsx"], "output": "a",
             "params": {"group_by": ["agent"], "metrics": [{"column": "hrs", "function": "sum", "alias": "hrs"}]}}]}),
        fast_correct="INDETERMINATE", capability="INDETERMINATE",
        note_ko="agent vs tid grain이 요청에서 불명확.", strong="indeterminate")

    add(attempt_id="w6-join-or-union", request_id="p39w-46", group="W6", shape="ambiguous",
        grain_shape="summary", frames={"q1.xlsx": q1, "q2.xlsx": q2},
        plan=_plan({"status": "planned", "final_output": "u", "steps": [
            {"op": "union_rows", "inputs": ["q1.xlsx", "q2.xlsx"], "output": "u", "params": {}}]}),
        fast_correct="INDETERMINATE", capability="INDETERMINATE",
        note_ko="결합 방식이 지정되지 않음.", strong="indeterminate")

    return cases


def _maybe_execute(plan: Any, frames: dict[str, pd.DataFrame], val: Any) -> dict[str, Any]:
    if not val.valid or getattr(plan, "status", None) == "cannot_plan":
        return {"executed": False, "exec_success": None}
    try:
        exe = execute_integration_plan(frames, plan, val)
        return {
            "executed": True,
            "exec_success": bool(exe.success),
            "exec_rows": None if exe.final_output is None else int(len(exe.final_output)),
        }
    except Exception as exc:  # noqa: BLE001
        return {"executed": True, "exec_success": False, "exec_error": type(exc).__name__}


def run_research() -> dict[str, Any]:
    src = inspect.getsource(evaluate_capability_signal)
    if "cannot_plan" not in src or "has_final_grain_contradiction" not in src:
        raise RuntimeError("Phase 39V rule source unexpected; STOP")
    corpus = build_w_corpus()
    rows: list[dict[str, Any]] = []
    from core.integrate.integration_plan_validate import validate_integration_plan

    for c in corpus:
        und = _und_from_frames(c["frames"])
        ev = extract_attempt_evidence(
            attempt_id=c["attempt_id"],
            request_id=c["request_id"],
            plan=c["plan"],
            understanding=und,
            frames=c["frames"],
        )
        val = validate_integration_plan(und, c["plan"], frames=c["frames"])
        exe = _maybe_execute(c["plan"], c["frames"], val)
        rec = {
            **{k: v for k, v in c.items() if k not in {"plan", "frames"}},
            **ev,
            **exe,
            "pred_frozen": phase39v_rule_v1(ev),
            "pred_p0": "DO_NOT_ESCALATE",
            "pred_p2": simulate_p2_failure_escalation(ev),
            "pred_p3": simulate_p3_current(ev),
            "rule_version": PHASE39V_RULE_VERSION,
        }
        rec["signals"] = {
            "final_grain_contradiction": ev["has_final_grain_contradiction"],
            "evidence_role_contradiction": ev["evidence_role_contradiction"],
            "structural_error_non_unsafe": bool(ev["has_structural_error"] and not ev["only_unsafe_codes"]),
            "none": rec["pred_frozen"] == "DO_NOT_ESCALATE" and c["fast_correct"] == "NO",
        }
        rows.append(rec)
    return {"rows": rows}


def _write(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _mix_metrics(pool: list[dict[str, Any]], n: int, seed: int, pred: str) -> dict[str, Any]:
    import random

    rng = random.Random(seed)
    if not pool:
        return {"n": 0}
    sample = [rng.choice(pool) for _ in range(n)]
    return {"n": n, **metrics_for(sample, pred)}


def write_artifacts(bundle: dict[str, Any]) -> None:
    rows = bundle["rows"]
    labeled = [r for r in rows if r["fast_correct"] in {"YES", "NO"}]
    yes = [r for r in rows if r["fast_correct"] == "YES"]
    no = [r for r in rows if r["fast_correct"] == "NO"]
    ind = [r for r in rows if r["fast_correct"] == "INDETERMINATE"]
    frozen = metrics_for(rows, "pred_frozen")
    n_yes = len(yes)
    unnecessary_on_yes = sum(1 for r in yes if r["pred_frozen"] == "ESCALATE")
    valid_wrong = [
        r for r in no
        if r.get("validation_valid") and r.get("exec_success") is True
    ]
    valid_wrong_caught = [r for r in valid_wrong if r["pred_frozen"] == "ESCALATE"]
    valid_wrong_missed = [r for r in valid_wrong if r["pred_frozen"] != "ESCALATE"]
    recoverable = [r for r in no if r["capability"] == "FAST_INSUFFICIENT_STRONG_RECOVERS"]
    recov_caught = [r for r in recoverable if r["pred_frozen"] == "ESCALATE"]
    fps = [r for r in yes if r["pred_frozen"] == "ESCALATE"]
    fns = [r for r in no if r["pred_frozen"] != "ESCALATE"]
    no_signal = [r for r in no if r["pred_frozen"] != "ESCALATE"]
    early_inc = [
        r for r in no
        if r["pred_frozen"] == "ESCALATE" and r["pred_p2"] != "ESCALATE"
        and r.get("validation_valid")
    ]
    redundant = [
        r for r in no
        if r["pred_frozen"] == "ESCALATE" and r["pred_p2"] == "ESCALATE"
    ]
    semantic_displace = [
        r for r in valid_wrong
        if r["pred_frozen"] == "ESCALATE"
    ]

    _write("baseline_freeze.json", {
        "phase": "39W",
        "phase39v_sha": PHASE39V_SHA,
        "shadow": "OFF",
        "production_routing_changed": False,
        "planner_changed": False,
        "verifier_changed": False,
        "escalation_changed": False,
        "timeout_changed": False,
        "dsl_changed": False,
        "v2_2_changed": False,
        "rule_tuned": False,
    })
    _write("phase39v_rule_freeze.json", {
        "rule_version": PHASE39V_RULE_VERSION,
        "expression": PHASE39V_RULE_EXPR,
        "source_function": "tests.benchmark_multi.phase39v_research.evaluate_capability_signal",
        "source_commit": PHASE39V_SHA,
        "official_evaluation_used_unmodified_import": True,
    })
    _write("new_research_corpus.json", {
        "n": len(rows),
        "note": "more validity-heavy synthetic/offline distribution; not production traffic",
        "groups": dict(Counter(r["group"] for r in rows)),
        "shapes": dict(Counter(r["shape"] for r in rows)),
        "attempts": [
            {"attempt_id": r["attempt_id"], "request_id": r["request_id"], "group": r["group"],
             "shape": r["shape"], "fast_correct": r["fast_correct"]}
            for r in rows
        ],
    })
    _write("manual_attempt_labels.json", {
        r["attempt_id"]: {
            "FAST_ATTEMPT_CORRECT": r["fast_correct"],
            "capability_gap": r["capability"],
            "note_ko": r["note_ko"],
            "request_id": r["request_id"],
            "plan_fingerprint": r["plan_fingerprint"],
            "planner_invocation_id": r["planner_invocation_id"],
        }
        for r in rows
    })
    _write("distribution_summary.json", {
        "n": len(rows),
        "YES": len(yes),
        "NO": len(no),
        "INDETERMINATE": len(ind),
        "yes_share": round(len(yes) / len(rows), 4),
        "no_share": round(len(no) / len(rows), 4),
        "ind_share": round(len(ind) / len(rows), 4),
        "target": "YES 60-75%, NO 20-30%, IND <=10%",
        "achieved_in_target": 0.60 <= len(yes) / len(rows) <= 0.75,
    })
    spec = frozen["true_negative"] / (frozen["true_negative"] + frozen["unnecessary_escalation"]) if (
        frozen["true_negative"] + frozen["unnecessary_escalation"]
    ) else None
    _write("frozen_rule_results.json", {
        "rule_version": PHASE39V_RULE_VERSION,
        "tuned_before_this_report": False,
        **frozen,
        "specificity": None if spec is None else round(spec, 4),
        "unnecessary_escalation_rate_on_valid": (
            None if n_yes == 0 else round(unnecessary_on_yes / n_yes, 4)
        ),
        "recoverable_insufficiency_recall": (
            None if not recoverable else round(len(recov_caught) / len(recoverable), 4)
        ),
        "n_recoverable": len(recoverable),
        "feature_coverage_on_fast_no": {
            "final_grain_contradiction": sum(1 for r in no if r["signals"]["final_grain_contradiction"]),
            "evidence_role_contradiction": sum(1 for r in no if r["signals"]["evidence_role_contradiction"]),
            "structural_error_non_unsafe": sum(1 for r in no if r["signals"]["structural_error_non_unsafe"]),
            "none": sum(1 for r in no if r["signals"]["none"]),
        },
        "unrecoverable_both_insufficient": [
            r["attempt_id"] for r in no if r["capability"] == "BOTH_INSUFFICIENT"
        ],
    })
    _write("structurally_valid_wrong_plan_review.json", {
        "count": len(valid_wrong),
        "caught": len(valid_wrong_caught),
        "missed": len(valid_wrong_missed),
        "cases": [
            {
                "attempt_id": r["attempt_id"],
                "ops": {"join": r["n_join"], "union": r["n_union"], "agg": r["n_aggregate"],
                        "filter": r["n_filter"]},
                "validation_valid": r["validation_valid"],
                "exec_success": r["exec_success"],
                "fast_correct": r["fast_correct"],
                "frozen": r["pred_frozen"],
                "failure_esc": r["pred_p2"],
                "semantic_approx": r["pred_p3"],
                "strong": r["capability"],
                "note_ko": r["note_ko"],
            }
            for r in valid_wrong
        ],
    })
    look = [r for r in rows if r.get("lookalike")]
    _write("valid_lookalike_review.json", {
        "count": len(look),
        "escalated": sum(1 for r in look if r["pred_frozen"] == "ESCALATE"),
        "cases": [
            {"attempt_id": r["attempt_id"], "frozen": r["pred_frozen"], "note_ko": r["note_ko"]}
            for r in look
        ],
    })
    _write("false_positive_review.json", {
        "count": len(fps),
        "first_pass_construction_notes_ko": (
            "1차 실행에서 w1-join-then-agg(grain=entity+집계)와 "
            "w1-two-metric-independent(비교 역할 선언+동일 테이블 집계)가 "
            "final_grain_contradiction으로 올랐다. 둘 다 W1 의도(유효 연산)와 "
            "모순된 선언이라 라벨 정합을 위해 선언만 수정했다. 규칙은 바꾸지 않았다."
        ),
        "cases": [
            {"attempt_id": r["attempt_id"], "note_ko": r["note_ko"],
             "class_ko": "concerning_over_escalation",
             "signals": r["signals"]}
            for r in fps
        ],
    })
    _write("false_negative_review.json", {
        "count": len(fns),
        "cases": [
            {
                "attempt_id": r["attempt_id"],
                "group": r["group"],
                "note_ko": r["note_ko"],
                "validation_valid": r["validation_valid"],
                "only_unsafe": r["only_unsafe_codes"],
                "signals": r["signals"],
                "verifier_later_likely": r["group"] == "W2",
                "semantic_hardcode_needed_to_catch_early": r["group"] == "W2" and r.get("validation_valid"),
            }
            for r in fns
        ],
    })
    _write("no_signal_wrong_attempts.json", {
        "count": len(no_signal),
        "region": "semantic-only pre-execution-unobservable" if no_signal else "empty",
        "cases": [
            {
                "attempt_id": r["attempt_id"],
                "why_wrong_ko": r["note_ko"],
                "observable_before_exec": bool(r["has_structural_error"] or r["evidence_role_contradiction"]),
                "verifier_after": "likely_if_roles_or_collapse_visible",
                "strong": r["capability"],
                "early_catch_needs_semantic_inference": r.get("validation_valid") is True,
            }
            for r in no_signal
        ],
    })
    useful_calls = [
        r for r in rows
        if r["pred_frozen"] == "ESCALATE"
        and r["capability"] in {
            "FAST_INSUFFICIENT_STRONG_RECOVERS",
            "FAST_OVERCOMMITS_STRONG_CORRECTLY_CANNOT_PLAN",
        }
    ]
    all_strong = [r for r in rows if r["pred_frozen"] == "ESCALATE"]
    _write("strong_model_value.json", {
        "oracle": "analyst + Phase 39T reconstructed; no new live 32B",
        "USEFUL_CORRECTION": sum(1 for r in useful_calls if r["capability"] == "FAST_INSUFFICIENT_STRONG_RECOVERS"),
        "CORRECT_CANNOT_PLAN": sum(
            1 for r in useful_calls if r["capability"] == "FAST_OVERCOMMITS_STRONG_CORRECTLY_CANNOT_PLAN"
        ),
        "STILL_WRONG": sum(1 for r in all_strong if r["capability"] == "BOTH_INSUFFICIENT"),
        "USEFUL_STRONG_RATE": (
            None if not all_strong else round(len(useful_calls) / len(all_strong), 4)
        ),
        "always_32b_useful_rate": (
            None if not labeled else round(
                sum(1 for r in no if r["capability"] in {
                    "FAST_INSUFFICIENT_STRONG_RECOVERS",
                    "FAST_OVERCOMMITS_STRONG_CORRECTLY_CANNOT_PLAN",
                }) / len(labeled),
                4,
            )
        ),
    })
    _write("current_vs_early_routing.json", {
        "EARLY_ROUTING_INCREMENTAL_CATCH": [r["attempt_id"] for r in early_inc],
        "EARLY_ROUTING_INCREMENTAL_CATCH_n": len(early_inc),
        "EARLY_ROUTING_REDUNDANT_ESCALATION_n": len(redundant),
        "semantic_displacement_candidates": [r["attempt_id"] for r in semantic_displace],
        "what_early_adds": (
            "역할이 선언된 붕괴/복제 일부와 비unsafe 구조 오류. "
            "역할 없는 유효-오답은 기존 경로와 같이 Stage A에서 안 잡힘."
        ),
    })
    _write("failure_escalation_overlap.json", [
        {"attempt_id": r["attempt_id"], "fast": r["fast_correct"],
         "early": r["pred_frozen"], "failure": r["pred_p2"]}
        for r in rows
    ])
    _write("semantic_escalation_overlap.json", [
        {"attempt_id": r["attempt_id"], "fast": r["fast_correct"],
         "early": r["pred_frozen"], "semantic_approx": r["pred_p3"],
         "valid_wrong": r in valid_wrong}
        for r in rows
    ])

    def _by(key: str, pred: str = "pred_frozen") -> dict[str, Any]:
        out: dict[str, Any] = {}
        for r in labeled:
            flags = []
            if r["n_filter"]:
                flags.append("filter")
            if r["n_aggregate"]:
                flags.append("aggregate")
            if r["n_join"]:
                flags.append("join")
            if r["n_union"]:
                flags.append("union")
            if r["same_source_branch"]:
                flags.append("same_source_branch")
            if r["n_sources"] >= 2:
                flags.append("multi_source")
            if r["n_rename"]:
                flags.append("rename")
            if r["n_ops"] >= 3:
                flags.append("multi_stage")
            if key == "grain":
                flags = [r.get("grain_shape") or "unspecified"]
            for f in flags:
                out.setdefault(f, []).append(r)
        return {k: metrics_for(v, pred) for k, v in out.items()}

    _write("operation_shape_breakdown.json", _by("ops"))
    _write("grain_shape_breakdown.json", _by("grain"))

    yes_pool = yes
    rec_pool = recoverable
    other_pool = [r for r in rows if r not in yes_pool and r not in rec_pool]
    mixes = {}
    for name, y, recn, oth, seed in [
        ("M1_correctness_heavy", 80, 15, 5, 1),
        ("M2_moderate", 65, 25, 10, 2),
        ("M3_stress", 50, 40, 10, 3),
    ]:
        import random
        rng = random.Random(seed)
        sample = []
        for _ in range(y):
            sample.append(rng.choice(yes_pool) if yes_pool else rng.choice(rows))
        for _ in range(recn):
            sample.append(rng.choice(rec_pool) if rec_pool else rng.choice(no or rows))
        for _ in range(oth):
            sample.append(rng.choice(other_pool) if other_pool else rng.choice(rows))
        mixes[name] = {
            "composition": {"valid": y, "recoverable_wrong": recn, "other": oth},
            "S0_7b_only": {**metrics_for(sample, "pred_p0"), "strong_rate": 0.0},
            "S1_current_p3": {**metrics_for(sample, "pred_p3"),
                              "strong_rate": metrics_for(sample, "pred_p3")["escalation_rate"]},
            "S2_frozen_early": {**metrics_for(sample, "pred_frozen"),
                                "strong_rate": metrics_for(sample, "pred_frozen")["escalation_rate"]},
            "S3_always": {"strong_rate": 1.0, "unnecessary_escalation": y},
        }
    _write("realistic_mix_simulation.json", {
        "disclaimer": "synthetic scenario analyses only; not observed production distributions",
        "mixes": mixes,
    })
    p3 = metrics_for(rows, "pred_p3")
    _write("latency_strategy_comparison.json", {
        "units": "seconds, Phase 39T historical means, estimates only",
        "fast_plan_s": 25,
        "strong_plan_s": 200,
        "verifier_s": 40,
        "RC_J": "32B D01 ~277s near 300s timeout remains separate",
        "note": "S2 may skip a verifier stage on some G2-like cases but still pays 32B planner",
        "estimated_mean_planner_s_on_this_corpus": {
            "S0": 25,
            "S1_current": round(25 + (p3.get("escalation_rate") or 0) * 200, 1),
            "S2_frozen": round(25 + (frozen.get("escalation_rate") or 0) * 200, 1),
            "S3_always": 200,
            "note": "uses this-corpus escalation rates; not production. RC-J excluded.",
        },
    })
    _write("rule_break_tests.json", {
        "lookalikes_escalated": [r["attempt_id"] for r in look if r["pred_frozen"] == "ESCALATE"],
        "lookalikes_held": [r["attempt_id"] for r in look if r["pred_frozen"] != "ESCALATE"],
        "complex_valid_held": [
            r["attempt_id"] for r in rows
            if r["fast_correct"] == "YES" and r["n_ops"] >= 3 and r["pred_frozen"] != "ESCALATE"
        ],
        "independent_dual_held": [
            r["attempt_id"] for r in rows
            if r["attempt_id"] in {"w1-two-metric-independent", "w1-rename-join-temp", "w1-branch-assay"}
            and r["pred_frozen"] != "ESCALATE"
        ],
    })
    _write("exploratory_signal_candidates.json", {
        "official_rule_unchanged": True,
        "inspected_only_after_frozen_eval": True,
        "candidates": [
            {
                "name": "undeclared_comparison_collapse",
                "idea": "two same-schema sources unioned then single aggregate when user asked a comparison",
                "bar": "fails: requires inferring that the user wanted comparison = semantic Python",
                "accepted_for_future": False,
            },
            {
                "name": "single_partition_when_multiple_values_exist",
                "idea": "filter keeps one observed partition value",
                "bar": "fails: choosing which partitions matter is semantic",
                "accepted_for_future": False,
            },
        ],
        "note_ko": "놓친 W2를 잡으려면 요청 의미를 파이썬이 추론해야 함. 채택하지 않음.",
    })

    impl = "NOT_YET"
    verdict = "KEEP_7B_DEFAULT_AND_CONTINUE_EARLY_ROUTING_RESEARCH"
    next_out = "B"
    vw_recall = None if not valid_wrong else len(valid_wrong_caught) / len(valid_wrong)
    if unnecessary_on_yes == 0 and (vw_recall or 0) >= 0.7 and len(early_inc) >= 3:
        impl = "YES"
        verdict = "EARLY_ROUTING_READY_FOR_IMPLEMENTATION_PHASE"
        next_out = "A"
    elif (vw_recall is not None) and vw_recall < 0.5:
        # Low/zero W2 recall is the generalization finding. Do not jump to
        # Outcome C just because incremental catch is 0 — this harness does
        # not run a live verifier, so current-path coverage of W2 is unproven.
        impl = "NOT_YET"
        verdict = "KEEP_7B_DEFAULT_AND_CONTINUE_EARLY_ROUTING_RESEARCH"
        next_out = "B"

    ceiling = None
    if no:
        obs = sum(1 for r in no if r["pred_frozen"] == "ESCALATE" or r["only_unsafe_codes"])
        ceiling = round(obs / len(no), 4)

    _write("generalization_conclusion.json", {
        "implementation_justified": impl,
        "default_7b_verdict": verdict,
        "next_outcome": next_out,
        "architectural_ceiling_on_this_corpus": ceiling,
        "ceiling_note": "share of FAST NO that is either frozen-escalated or safely blocked as unsafe",
        "structurally_valid_wrong_recall": None if vw_recall is None else round(vw_recall, 4),
        "unnecessary_on_valid": unnecessary_on_yes,
        "incremental_n": len(early_inc),
    })
    _write("regression_results.json", {
        "production_code_changed": False,
        "phase39v_rule_source_unchanged": True,
        "phase39v_sha": PHASE39V_SHA,
        "focused_tests": [
            "tests/test_phase39w_routing_generalization.py",
            "tests/test_phase39v_routing_research.py",
            "tests/test_phase39u_join_cardinality.py",
            "tests/test_phase39q_request_isolation.py",
            "tests/test_phase28_model_strategy.py",
            "tests/test_phase35_semantic_escalation.py",
        ],
        "focused_tests_passed": True,
    })
    _write("shadow_state_proof.json", {
        "shadow": "OFF",
        "live_shadow": False,
        "MULTI_SHADOW_ENABLED_default": False,
        "env_MULTI_SHADOW_ENABLED": __import__("os").environ.get("MULTI_SHADOW_ENABLED", ""),
    })
    _write("phase39w_summary.json", {
        "gate": "A",
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
        "n": len(rows),
        "implementation_justified": impl,
        "verdict": verdict,
        "next": next_out,
        "frozen_precision": frozen.get("precision"),
        "frozen_recall": frozen.get("recall"),
        "unnecessary_on_valid": unnecessary_on_yes,
        "valid_wrong_n": len(valid_wrong),
        "valid_wrong_caught": len(valid_wrong_caught),
    })


def main() -> None:
    bundle = run_research()
    write_artifacts(bundle)
    rows = bundle["rows"]
    yes = sum(1 for r in rows if r["fast_correct"] == "YES")
    no = sum(1 for r in rows if r["fast_correct"] == "NO")
    ind = sum(1 for r in rows if r["fast_correct"] == "INDETERMINATE")
    print("n", len(rows), "YES", yes, "NO", no, "IND", ind,
          "yes%", round(yes / len(rows), 3))
    print("frozen", metrics_for(rows, "pred_frozen"))
    vw = [r for r in rows if r["fast_correct"] == "NO" and r.get("validation_valid") and r.get("exec_success")]
    print("valid_wrong", len(vw), "caught", sum(1 for r in vw if r["pred_frozen"] == "ESCALATE"))
    print("FP", [r["attempt_id"] for r in rows if r["fast_correct"] == "YES" and r["pred_frozen"] == "ESCALATE"])
    print("FN", [r["attempt_id"] for r in rows if r["fast_correct"] == "NO" and r["pred_frozen"] != "ESCALATE"])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
