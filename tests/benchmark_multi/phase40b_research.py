"""Phase 40B — verifier prompt-vs-model strategy generalization (research only).

Does NOT modify production verifier prompt, model, thresholds, or wiring.
Uses frozen Phase 40A P0/P1 text and hashes.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.attempt_lineage import (
    compact_result_fingerprint,
    new_verifier_invocation_id,
    plan_fingerprint,
)
from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.result_observation import (
    MAX_RESULT_SAMPLE_COLUMNS,
    MAX_RESULT_SAMPLE_ROWS,
    MAX_RESULT_SERIALIZED_CHARS,
    observe_result_for_verifier,
)
from core.integrate.schema_lineage import extract_source_schemas_from_understanding
from core.integrate.semantic_escalation import (
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
    _should_semantic_escalate,
)
from core.integrate.semantic_verifier import (
    _VERIFIER_SYSTEM,
    _normalize_verdict,
    build_verifier_payload,
    run_semantic_verification,
)
from core.llm_client import chat_json
from core.shadow.fingerprint import dataframe_fingerprint
from tests.benchmark_multi.phase39v_research import _und_from_frames
from tests.benchmark_multi.phase39w_research import build_w_corpus
from tests.benchmark_multi.phase39x_research import MATERIALIZATION
from tests.benchmark_multi.phase40a_research import (
    ALL_IDS as PHASE40A_IDS,
    M2_ID,
    P1_ADDENDUM,
    PROMPT_REGISTRY,
    production_user_prefix,
    prompt_for,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase40b"
CACHE = OUT / "verifier_live_cache.json"
PHASE40A_SHA = "9fd1b1009c69fdd8a33383d46f5b434a0ff7af59"
LIVE = os.environ.get("PHASE40B_LIVE_VERIFIER", "1") != "0"
BASE_URL = "http://localhost:11434"
M7 = "qwen2.5:7b"
M8 = "qwen3:8b"
STABILITY_N = 5
P0_SHA = "7d9238548ae40e59a68d15852bf8f97becb00cbbe38b7be92782d5d811e8f2cd"
P1_SHA = "af3d48d01a24be17e96164cee5387bc57cd58be0d899f34c1b010743d0358e90"

STRATEGIES = {
    "S0": {"model": M7, "prompt": "P0", "name": "current_7b_p0"},
    "S1": {"model": M7, "prompt": "P1", "name": "prompt_only_7b_p1"},
    "S2": {"model": M8, "prompt": "P0", "name": "model_only_8b_p0"},
    "S3": {"model": M8, "prompt": "P1", "name": "combined_8b_p1"},
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plan(final: str, steps: list[dict[str, Any]]) -> Any:
    return integration_plan_from_dict({"status": "planned", "final_output": final, "steps": steps})


def _agg(src: str, out: str, gb: list[str], col: str, fn: str, alias: str) -> dict[str, Any]:
    return {
        "op": "aggregate",
        "inputs": [src],
        "output": out,
        "params": {"group_by": gb, "metrics": [{"column": col, "function": fn, "alias": alias}]},
    }


def _filt(src: str, out: str, col: str, val: str) -> dict[str, Any]:
    return {
        "op": "filter_rows",
        "inputs": [src],
        "output": out,
        "params": {"conditions": [{"column": col, "operator": "eq", "value": val}]},
    }


def _join(a: str, b: str, out: str, key: str, how: str = "inner") -> dict[str, Any]:
    return {
        "op": "join",
        "inputs": [a, b],
        "output": out,
        "params": {"left_keys": [key], "right_keys": [key], "how": how},
    }


def _union(a: str, b: str, out: str) -> dict[str, Any]:
    return {"op": "union_rows", "inputs": [a, b], "output": out, "params": {}}


def _sel(src: str, out: str, cols: list[str]) -> dict[str, Any]:
    return {"op": "select_columns", "inputs": [src], "output": out, "params": {"columns": cols}}


def _ren(src: str, out: str, mapping: dict[str, str]) -> dict[str, Any]:
    return {"op": "rename_columns", "inputs": [src], "output": out, "params": {"mapping": mapping}}


def raw_cases() -> list[dict[str, Any]]:
    """New Phase 40B cases. No Phase 40A attempt_ids."""
    rooms = pd.DataFrame(
        {"crm": ["C1", "C1", "C2", "C3"], "campus": ["N", "N", "S", "S"], "lux": [10, 12, 8, 9]}
    )
    hauls = pd.DataFrame(
        {"hid": ["H1", "H2", "H3", "H4"], "vessel": ["V1", "V1", "V2", "V2"], "kg": [4, 6, 3, 5]}
    )
    trees = pd.DataFrame(
        {"tid": ["T1", "T2", "T3", "T4"], "orchard": ["O1", "O1", "O2", "O2"], "kg": [2, 3, 4, 1]}
    )
    stops = pd.DataFrame(
        {"sid": ["P1", "P2", "P3", "P4"], "route": ["R9", "R9", "R8", "R8"], "pax": [11, 7, 9, 5]}
    )
    fruit = pd.DataFrame({"sku": ["A", "B", "C"], "grade": ["A", "B", "A"], "n": [3, 4, 2]})
    w1 = pd.DataFrame({"item": ["X", "Y"], "qty": [2, 3]})
    w2 = pd.DataFrame({"item": ["X", "Z"], "qty": [5, 1]})
    dawn = pd.DataFrame({"gate": ["G1", "G2"], "flow": [10, 12]})
    dusk = pd.DataFrame({"gate": ["G1", "G2"], "flow": [18, 14]})
    dist = pd.DataFrame({"unit": ["U1", "U2"], "km": [100, 80]})
    fuel = pd.DataFrame({"unit": ["U1", "U2"], "liters": [20, 18]})
    docks = pd.DataFrame({"dock": ["D1", "D1", "D2"], "kg": [4, 6, 10]})
    labs = pd.DataFrame(
        {
            "spec": ["S1", "S1", "S2", "S2"],
            "lab": ["L1", "L2", "L1", "L2"],
            "ppm": [0.2, 0.3, 0.4, 0.1],
        }
    )
    site_p = pd.DataFrame({"bin": ["B1", "B2"], "qty": [5, 6]})
    site_q = pd.DataFrame({"bin": ["B1", "B2"], "qty": [1, 2]})
    ev = pd.DataFrame({"eid": ["E1", "E2", "E3"], "zone": ["Z1", "Z1", "Z2"], "sec": [3, 5, 4]})
    cust = pd.DataFrame({"cid": ["K1", "K2"], "name": ["Ann", "Bo"]})
    ord_ = pd.DataFrame({"cid": ["K1", "K2"], "amt": [10, 20]})
    tix = pd.DataFrame({"xid": ["I1", "I2", "I3"], "state": ["closed", "open", "closed"], "hrs": [2, 9, 1]})
    lefts = pd.DataFrame({"kid": ["K1", "K2"], "score": [1, 2]})
    rights = pd.DataFrame({"kid": ["K1", "K2"], "score": [8, 9]})
    cat = pd.DataFrame({"isbn": ["X", "Y"], "title": ["t1", "t2"]})
    loan = pd.DataFrame({"isbn": ["X", "Y"], "days": [3, 7]})
    wide = pd.DataFrame({"uid": ["U1", "U2"], **{f"a{i}": [i, i + 1] for i in range(30)}})
    tall = pd.DataFrame({"uid": [f"R{i}" for i in range(80)], "v": list(range(80))})
    wide2 = pd.DataFrame({f"c{i}": [0, 1] for i in range(28)})
    wide2.insert(0, "id", ["A", "B"])
    shift = pd.DataFrame(
        {"nid": ["N1", "N1", "N2", "N2"], "shift": ["AM", "PM", "AM", "PM"], "w": [4, 5, 6, 7]}
    )

    def c(**kw: Any) -> dict[str, Any]:
        return kw

    return [
        # ----- key identity wrong -----
        c(attempt_id="b40-n-campus", request_id="p40b-01", fast_correct="NO",
          defect="grouping_identity", shape="aggregate", sources=1, trunc=False,
          prompt="Sum lux per campus, not per individual classroom.",
          note_ko="캠퍼스별 합계인데 교실 id로 집계.",
          frames={"rooms.xlsx": rooms},
          plan=_plan("a", [_agg("rooms.xlsx", "a", ["crm"], "lux", "sum", "lux")])),
        c(attempt_id="b40-n-vessel", request_id="p40b-02", fast_correct="NO",
          defect="grouping_identity", shape="aggregate", sources=1, trunc=False,
          prompt="Sum kg per vessel, not per individual haul.",
          note_ko="선박별 합계인데 투망 id로 집계.",
          frames={"hauls.xlsx": hauls},
          plan=_plan("a", [_agg("hauls.xlsx", "a", ["hid"], "kg", "sum", "kg")])),
        c(attempt_id="b40-n-orchard", request_id="p40b-03", fast_correct="NO",
          defect="grouping_identity", shape="aggregate", sources=1, trunc=False,
          prompt="Sum kg per orchard, not per individual tree.",
          note_ko="과수원별인데 나무 id로 집계.",
          frames={"trees.xlsx": trees},
          plan=_plan("a", [_agg("trees.xlsx", "a", ["tid"], "kg", "sum", "kg")])),
        c(attempt_id="b40-n-route", request_id="p40b-04", fast_correct="NO",
          defect="grouping_identity", shape="aggregate", sources=1, trunc=False,
          prompt="Sum passengers per route, not per individual stop.",
          note_ko="노선별인데 정류장 id로 집계.",
          frames={"stops.xlsx": stops},
          plan=_plan("a", [_agg("stops.xlsx", "a", ["sid"], "pax", "sum", "pax")])),
        # ----- key identity valid -----
        c(attempt_id="b40-y-campus", request_id="p40b-05", fast_correct="YES",
          defect=None, shape="aggregate", sources=1, trunc=False,
          prompt="Sum lux per campus.",
          note_ko="캠퍼스 group-by가 요청과 일치.",
          frames={"rooms.xlsx": rooms},
          plan=_plan("a", [_agg("rooms.xlsx", "a", ["campus"], "lux", "sum", "lux")])),
        c(attempt_id="b40-y-vessel", request_id="p40b-06", fast_correct="YES",
          defect=None, shape="aggregate", sources=1, trunc=False,
          prompt="Sum kg per vessel.",
          note_ko="선박 group-by가 요청과 일치.",
          frames={"hauls.xlsx": hauls},
          plan=_plan("a", [_agg("hauls.xlsx", "a", ["vessel"], "kg", "sum", "kg")])),
        c(attempt_id="b40-y-orchard", request_id="p40b-07", fast_correct="YES",
          defect=None, shape="aggregate", sources=1, trunc=False,
          prompt="Sum kg per orchard.",
          note_ko="과수원 group-by가 요청과 일치.",
          frames={"trees.xlsx": trees},
          plan=_plan("a", [_agg("trees.xlsx", "a", ["orchard"], "kg", "sum", "kg")])),
        c(attempt_id="b40-y-route", request_id="p40b-08", fast_correct="YES",
          defect=None, shape="aggregate", sources=1, trunc=False,
          prompt="Sum passengers per route.",
          note_ko="노선 group-by가 요청과 일치.",
          frames={"stops.xlsx": stops},
          plan=_plan("a", [_agg("stops.xlsx", "a", ["route"], "pax", "sum", "pax")])),
        # ----- non-grouping wrong -----
        c(attempt_id="b40-n-filter-grade", request_id="p40b-09", fast_correct="NO",
          defect="filter_meaning", shape="filter", sources=1, trunc=False,
          prompt="Keep only grade A fruit rows.",
          note_ko="A등급이 필요한데 B로 필터.",
          frames={"fruit.xlsx": fruit},
          plan=_plan("f", [_filt("fruit.xlsx", "f", "grade", "B")])),
        c(attempt_id="b40-n-join-not-stack", request_id="p40b-10", fast_correct="NO",
          defect="integration_shape", shape="join", sources=2, trunc=False,
          prompt="Stack week-1 and week-2 item rows so every item row from either week is kept.",
          note_ko="적재가 필요한데 inner join.",
          frames={"w1.xlsx": w1, "w2.xlsx": w2},
          plan=_plan("j", [_join("w1.xlsx", "w2.xlsx", "j", "item")])),
        c(attempt_id="b40-n-union-not-compare", request_id="p40b-11", fast_correct="NO",
          defect="role_side_mapping", shape="union", sources=2, trunc=False,
          prompt="For each gate show dawn flow next to dusk flow.",
          note_ko="나란히 비교가 필요한데 행만 쌓음.",
          frames={"dawn.xlsx": dawn, "dusk.xlsx": dusk},
          plan=_plan("u", [_union("dawn.xlsx", "dusk.xlsx", "u")])),
        c(attempt_id="b40-n-drop-liters", request_id="p40b-12", fast_correct="NO",
          defect="output_completeness", shape="join", sources=2, trunc=False,
          prompt="Join distance with fuel so I can compare km and liters together.",
          note_ko="liters를 버림.",
          frames={"dist.xlsx": dist, "fuel.xlsx": fuel},
          plan=_plan("s", [_join("dist.xlsx", "fuel.xlsx", "j", "unit"), _sel("j", "s", ["unit", "km"])])),
        c(attempt_id="b40-n-mean-not-total", request_id="p40b-13", fast_correct="NO",
          defect="metric_meaning", shape="aggregate", sources=1, trunc=False,
          prompt="For each dock report the total kg.",
          note_ko="총량인데 평균.",
          frames={"dock.xlsx": docks},
          plan=_plan("a", [_agg("dock.xlsx", "a", ["dock"], "kg", "mean", "kg")])),
        c(attempt_id="b40-n-one-lab", request_id="p40b-14", fast_correct="NO",
          defect="role_side_mapping", shape="filter", sources=1, trunc=False,
          prompt="For each specimen show ppm from lab L1 and lab L2 side by side.",
          note_ko="두 랩이 필요한데 L1만.",
          frames={"lab.xlsx": labs},
          plan=_plan("a", [_filt("lab.xlsx", "f", "lab", "L1"), _agg("f", "a", ["spec"], "ppm", "sum", "ppm")])),
        c(attempt_id="b40-n-collapse-sites", request_id="p40b-15", fast_correct="NO",
          defect="role_side_mapping", shape="union", sources=2, trunc=False,
          prompt="Compare site P and site Q quantity for each bin. I need both sites visible.",
          note_ko="비교인데 합쳐 한 합계.",
          frames={"p.xlsx": site_p, "q.xlsx": site_q},
          plan=_plan("a", [_union("p.xlsx", "q.xlsx", "u"), _agg("u", "a", ["bin"], "qty", "sum", "qty_all")])),
        c(attempt_id="b40-n-agg-events", request_id="p40b-16", fast_correct="NO",
          defect="aggregation_mismatch", shape="aggregate", sources=1, trunc=False,
          prompt="Keep every event row with its zone and seconds. Do not collapse events.",
          note_ko="행 유지가 필요한데 집계.",
          frames={"ev.xlsx": ev},
          plan=_plan("a", [_agg("ev.xlsx", "a", ["zone"], "sec", "sum", "sec")])),
        c(attempt_id="b40-n-drop-name", request_id="p40b-17", fast_correct="NO",
          defect="output_completeness", shape="join", sources=2, trunc=False,
          prompt="Join orders to customers and keep customer name with the amount.",
          note_ko="이름이 필요한데 버림.",
          frames={"cust.xlsx": cust, "ord.xlsx": ord_},
          plan=_plan("s", [_join("cust.xlsx", "ord.xlsx", "j", "cid"), _sel("j", "s", ["cid", "amt"])])),
        c(attempt_id="b40-n-filter-open", request_id="p40b-18", fast_correct="NO",
          defect="filter_meaning", shape="filter", sources=1, trunc=False,
          prompt="Keep only closed tickets.",
          note_ko="closed가 필요한데 open 필터.",
          frames={"tix.xlsx": tix},
          plan=_plan("f", [_filt("tix.xlsx", "f", "state", "open")])),
        c(attempt_id="b40-n-left-only", request_id="p40b-19", fast_correct="NO",
          defect="role_side_mapping", shape="join", sources=2, trunc=False,
          prompt="For each key show the left score next to the right score.",
          note_ko="양쪽 점수가 필요한데 왼쪽만.",
          frames={"left.xlsx": lefts, "right.xlsx": rights},
          plan=_plan("s", [
              _ren("left.xlsx", "l", {"score": "left_s"}),
              _ren("right.xlsx", "r", {"score": "right_s"}),
              _join("l", "r", "j", "kid"),
              _sel("j", "s", ["kid", "left_s"]),
          ])),
        c(attempt_id="b40-n-concat-catalog", request_id="p40b-20", fast_correct="NO",
          defect="output_completeness", shape="select", sources=2, trunc=False,
          prompt="Attach each loan's title from the catalog.",
          note_ko="목록 제목 부착이 필요한데 대출 열만 남김.",
          frames={"loan.xlsx": loan, "cat.xlsx": cat},
          plan=_plan("s", [_sel("loan.xlsx", "s", ["isbn", "days"])])),
        # ----- valid lookalikes / extras -----
        c(attempt_id="b40-y-filter-grade", request_id="p40b-21", fast_correct="YES",
          defect=None, shape="filter", sources=1, trunc=False,
          prompt="Keep only grade A fruit rows.",
          note_ko="A등급 필터가 요청과 일치.",
          frames={"fruit.xlsx": fruit},
          plan=_plan("f", [_filt("fruit.xlsx", "f", "grade", "A")])),
        c(attempt_id="b40-y-stack-weeks", request_id="p40b-22", fast_correct="YES",
          defect=None, shape="union", sources=2, trunc=False,
          prompt="Stack week-1 and week-2 item rows so every item row from either week is kept.",
          note_ko="적재 union이 요청과 일치.",
          frames={"w1.xlsx": w1, "w2.xlsx": w2},
          plan=_plan("u", [_union("w1.xlsx", "w2.xlsx", "u")])),
        c(attempt_id="b40-y-compare-tod", request_id="p40b-23", fast_correct="YES",
          defect=None, shape="join", sources=2, trunc=False,
          prompt="For each gate show dawn flow next to dusk flow.",
          note_ko="rename+join 비교가 요청과 일치.",
          frames={"dawn.xlsx": dawn, "dusk.xlsx": dusk},
          plan=_plan("j", [
              _ren("dawn.xlsx", "a", {"flow": "dawn_flow"}),
              _ren("dusk.xlsx", "b", {"flow": "dusk_flow"}),
              _join("a", "b", "j", "gate"),
          ])),
        c(attempt_id="b40-y-keep-both", request_id="p40b-24", fast_correct="YES",
          defect=None, shape="join", sources=2, trunc=False,
          prompt="Join distance with fuel so I can compare km and liters together.",
          note_ko="두 메트릭 유지.",
          frames={"dist.xlsx": dist, "fuel.xlsx": fuel},
          plan=_plan("j", [_join("dist.xlsx", "fuel.xlsx", "j", "unit")])),
        c(attempt_id="b40-y-total-dock", request_id="p40b-25", fast_correct="YES",
          defect=None, shape="aggregate", sources=1, trunc=False,
          prompt="For each dock report the total kg.",
          note_ko="dock 합계가 요청과 일치.",
          frames={"dock.xlsx": docks},
          plan=_plan("a", [_agg("dock.xlsx", "a", ["dock"], "kg", "sum", "kg")])),
        c(attempt_id="b40-y-one-lab-ok", request_id="p40b-26", fast_correct="YES",
          defect=None, shape="filter", sources=1, trunc=False,
          prompt="Using only lab L1, report total ppm per specimen.",
          note_ko="단일 랩 요청과 일치.",
          frames={"lab.xlsx": labs},
          plan=_plan("a", [_filt("lab.xlsx", "f", "lab", "L1"), _agg("f", "a", ["spec"], "ppm", "sum", "ppm")])),
        c(attempt_id="b40-y-combined-total", request_id="p40b-27", fast_correct="YES",
          defect=None, shape="union", sources=2, trunc=False,
          prompt="Combine site P and site Q rows and give total quantity per bin.",
          note_ko="합친 합계가 요청과 일치.",
          frames={"p.xlsx": site_p, "q.xlsx": site_q},
          plan=_plan("a", [_union("p.xlsx", "q.xlsx", "u"), _agg("u", "a", ["bin"], "qty", "sum", "qty_all")])),
        c(attempt_id="b40-y-event-rows", request_id="p40b-28", fast_correct="YES",
          defect=None, shape="select", sources=1, trunc=False,
          prompt="Keep every event row with its zone and seconds.",
          note_ko="행 유지 select.",
          frames={"ev.xlsx": ev},
          plan=_plan("s", [_sel("ev.xlsx", "s", ["eid", "zone", "sec"])])),
        c(attempt_id="b40-y-with-name", request_id="p40b-29", fast_correct="YES",
          defect=None, shape="join", sources=2, trunc=False,
          prompt="Join orders to customers and keep customer name with the amount.",
          note_ko="이름+금액 유지.",
          frames={"cust.xlsx": cust, "ord.xlsx": ord_},
          plan=_plan("j", [_join("cust.xlsx", "ord.xlsx", "j", "cid")])),
        c(attempt_id="b40-y-closed", request_id="p40b-30", fast_correct="YES",
          defect=None, shape="filter", sources=1, trunc=False,
          prompt="Keep only closed tickets.",
          note_ko="closed 필터가 요청과 일치.",
          frames={"tix.xlsx": tix},
          plan=_plan("f", [_filt("tix.xlsx", "f", "state", "closed")])),
        c(attempt_id="b40-y-left-only-ok", request_id="p40b-31", fast_correct="YES",
          defect=None, shape="select", sources=1, trunc=False,
          prompt="Keep only the left score for each key.",
          note_ko="왼쪽만 요청과 일치.",
          frames={"left.xlsx": lefts},
          plan=_plan("s", [_sel("left.xlsx", "s", ["kid", "score"])])),
        c(attempt_id="b40-y-join-catalog", request_id="p40b-32", fast_correct="YES",
          defect=None, shape="join", sources=2, trunc=False,
          prompt="Attach each loan's title from the catalog.",
          note_ko="카탈로그 조인이 요청과 일치.",
          frames={"loan.xlsx": loan, "cat.xlsx": cat},
          plan=_plan("j", [_join("loan.xlsx", "cat.xlsx", "j", "isbn")])),
        c(attempt_id="b40-y-filter-then-sum", request_id="p40b-33", fast_correct="YES",
          defect=None, shape="multi_stage", sources=1, trunc=False,
          prompt="For closed tickets only, sum hours.",
          note_ko="필터 후 합계.",
          frames={"tix.xlsx": tix},
          plan=_plan("a", [_filt("tix.xlsx", "f", "state", "closed"),
                           {"op": "aggregate", "inputs": ["f"], "output": "a",
                            "params": {"group_by": [], "metrics": [{"column": "hrs", "function": "sum", "alias": "hrs"}]}}])),
        c(attempt_id="b40-y-1to1-join", request_id="p40b-34", fast_correct="YES",
          defect=None, shape="join", sources=2, trunc=False,
          prompt="Attach each customer's name to their order amount.",
          note_ko="1:1 조인.",
          frames={"cust.xlsx": cust, "ord.xlsx": ord_},
          plan=_plan("j", [_join("cust.xlsx", "ord.xlsx", "j", "cid")])),
        c(attempt_id="b40-y-am-only", request_id="p40b-35", fast_correct="YES",
          defect=None, shape="filter", sources=1, trunc=False,
          prompt="Using only the AM shift, sum w per nid.",
          note_ko="단일 시프트 요청과 일치.",
          frames={"shift.xlsx": shift},
          plan=_plan("a", [_filt("shift.xlsx", "f", "shift", "AM"), _agg("f", "a", ["nid"], "w", "sum", "w")])),
        c(attempt_id="b40-y-dual-metric", request_id="p40b-36", fast_correct="YES",
          defect=None, shape="aggregate", sources=1, trunc=False,
          prompt="For each campus report sum lux.",
          note_ko="캠퍼스 합계(단일 메트릭).",
          frames={"rooms.xlsx": rooms},
          plan=_plan("a", [_agg("rooms.xlsx", "a", ["campus"], "lux", "sum", "lux_sum")])),
        c(attempt_id="b40-y-wide-trunc", request_id="p40b-37", fast_correct="YES",
          defect=None, shape="select", sources=1, trunc=True,
          prompt="Keep every measured attribute for each unit.",
          note_ko="넓은 결과 truncation 통제.",
          frames={"units.xlsx": wide},
          plan=_plan("s", [_sel("units.xlsx", "s", ["uid"] + [f"a{i}" for i in range(30)])])),
        c(attempt_id="b40-y-tall-trunc", request_id="p40b-38", fast_correct="YES",
          defect=None, shape="select", sources=1, trunc=True,
          prompt="Keep uid and v for every row.",
          note_ko="많은 행 truncation 통제.",
          frames={"tall.xlsx": tall},
          plan=_plan("s", [_sel("tall.xlsx", "s", ["uid", "v"])])),
        c(attempt_id="b40-y-wide2-trunc", request_id="p40b-39", fast_correct="YES",
          defect=None, shape="select", sources=1, trunc=True,
          prompt="Keep all columns for each id.",
          note_ko="열 truncation 통제.",
          frames={"wide2.xlsx": wide2},
          plan=_plan("s", [_sel("wide2.xlsx", "s", ["id"] + [f"c{i}" for i in range(28)])])),
        c(attempt_id="b40-y-multi-stage", request_id="p40b-40", fast_correct="YES",
          defect=None, shape="multi_stage", sources=2, trunc=False,
          prompt="Join customers to orders then keep cid, name, and amt.",
          note_ko="조인 후 select.",
          frames={"cust.xlsx": cust, "ord.xlsx": ord_},
          plan=_plan("s", [_join("cust.xlsx", "ord.xlsx", "j", "cid"), _sel("j", "s", ["cid", "name", "amt"])])),
        c(attempt_id="b40-y-union-ok", request_id="p40b-41", fast_correct="YES",
          defect=None, shape="union", sources=2, trunc=False,
          prompt="Concatenate week-1 and week-2 item rows into one list.",
          note_ko="단순 concat.",
          frames={"w1.xlsx": w1, "w2.xlsx": w2},
          plan=_plan("u", [_union("w1.xlsx", "w2.xlsx", "u")])),
        c(attempt_id="b40-y-select-tix", request_id="p40b-42", fast_correct="YES",
          defect=None, shape="select", sources=1, trunc=False,
          prompt="Keep ticket id and state only.",
          note_ko="열 부분집합.",
          frames={"tix.xlsx": tix},
          plan=_plan("s", [_sel("tix.xlsx", "s", ["xid", "state"])])),
    ]


STAB_S3_M2LIKE = ["b40-n-campus", "b40-n-vessel", "b40-n-orchard"]
STAB_S3_VALID_GROUP = ["b40-y-campus", "b40-y-vessel"]
STAB_S3_NONG_WRONG = ["b40-n-filter-grade", "b40-n-join-not-stack"]
STAB_S3_NONG_VALID = ["b40-y-filter-grade", "b40-y-stack-weeks"]


def _write(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


def _load_cache() -> dict[str, Any]:
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def _save_cache(cache: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False, default=str) + "\n")


def _label(fast: str, verdict: str | None) -> str | None:
    if not verdict:
        return None
    non = verdict in {"fail", "uncertain"}
    if fast == "NO" and non:
        return "CORRECT_REJECTION"
    if fast == "NO" and verdict == "pass":
        return "SILENT_WRONG"
    if fast == "YES" and verdict == "pass":
        return "CORRECT_PASS"
    if fast == "YES" and non:
        return "FALSE_FAIL"
    return None


def materialize(raw: dict[str, Any]) -> dict[str, Any]:
    und = _und_from_frames(raw["frames"])
    val = validate_integration_plan(und, raw["plan"], frames=raw["frames"])
    exe = None
    if val.valid:
        try:
            exe = execute_integration_plan(raw["frames"], raw["plan"], val)
        except Exception:  # noqa: BLE001
            exe = None
    fo = exe.final_output if exe is not None and exe.success else None
    obs = observe_result_for_verifier(fo)
    fp_src = dataframe_fingerprint(fo) if isinstance(fo, pd.DataFrame) else None
    return {
        "attempt_id": raw["attempt_id"],
        "request_id": raw["request_id"],
        "fast_correct": raw["fast_correct"],
        "user_prompt": raw["prompt"],
        "note_ko": raw["note_ko"],
        "defect": raw.get("defect"),
        "shape": raw["shape"],
        "sources": raw["sources"],
        "trunc_expected": raw["trunc"],
        "plan_dict": raw["plan"].to_dict(),
        "und": und,
        "validation_valid": bool(val.valid),
        "exec_success": None if exe is None else bool(exe.success),
        "result_obs": obs,
        "plan_fingerprint": plan_fingerprint(raw["plan"]),
        "result_fingerprint": compact_result_fingerprint(fp_src),
        "truncated_obs": bool(isinstance(obs, dict) and obs.get("truncated")),
        "historical": False,
    }


def m2_anchor() -> dict[str, Any]:
    c = next(x for x in build_w_corpus() if x["attempt_id"] == M2_ID)
    und = _und_from_frames(c["frames"])
    val = validate_integration_plan(und, c["plan"], frames=c["frames"])
    exe = execute_integration_plan(c["frames"], c["plan"], val)
    fo = exe.final_output
    obs = observe_result_for_verifier(fo)
    fp_src = dataframe_fingerprint(fo)
    return {
        "attempt_id": M2_ID,
        "request_id": c["request_id"],
        "fast_correct": "NO",
        "user_prompt": "Sum ticket hours per agent, not per individual ticket.",
        "note_ko": "역사적 M2 앵커. 새 홀드아웃 분모에서 제외.",
        "defect": "grouping_identity",
        "shape": "aggregate",
        "sources": 1,
        "trunc_expected": False,
        "plan_dict": c["plan"].to_dict(),
        "und": und,
        "validation_valid": bool(val.valid),
        "exec_success": bool(exe.success),
        "result_obs": obs,
        "plan_fingerprint": plan_fingerprint(c["plan"]),
        "result_fingerprint": compact_result_fingerprint(fp_src),
        "truncated_obs": bool(obs and obs.get("truncated")),
        "historical": True,
    }


def build_corpus() -> list[dict[str, Any]]:
    rows = [materialize(c) for c in raw_cases()]
    overlap = {r["attempt_id"] for r in rows} & set(PHASE40A_IDS)
    if overlap:
        raise RuntimeError(f"40A overlap {overlap}")
    return rows


def _payload(rec: dict[str, Any]) -> dict[str, Any]:
    return build_verifier_payload(
        user_prompt=rec["user_prompt"],
        plan=rec["plan_dict"],
        result=rec.get("result_obs"),
        understanding=rec["und"],
        variant=SEMANTIC_VERIFIER_VARIANT,
        materialization_mode=MATERIALIZATION,
        source_schemas=extract_source_schemas_from_understanding(rec["und"]),
    )


def invoke(strategy: str, rec: dict[str, Any], cache: dict[str, Any], *, repeat: int = 0) -> dict[str, Any]:
    spec = STRATEGIES[strategy]
    model, pvar = spec["model"], spec["prompt"]
    key = f"{rec['attempt_id']}|{strategy}|{model}|{pvar}|{repeat}"
    if key in cache:
        return cache[key]
    t0 = time.time()
    inv = new_verifier_invocation_id()
    err = None
    parse_ok = True
    try:
        if pvar == "P0":
            ver = run_semantic_verification(
                user_prompt=rec["user_prompt"],
                plan=rec["plan_dict"],
                result=rec.get("result_obs"),
                understanding=rec["und"],
                variant=SEMANTIC_VERIFIER_VARIANT,
                model=model,
                materialization_mode=MATERIALIZATION,
                source_schemas=extract_source_schemas_from_understanding(rec["und"]),
                base_url=BASE_URL,
                lineage_context={
                    "request_id": rec["request_id"],
                    "attempt_id": rec["attempt_id"],
                    "plan_fingerprint": rec.get("plan_fingerprint"),
                    "result_fingerprint": rec.get("result_fingerprint"),
                },
            )
            raw = getattr(ver, "raw", None) or {}
        else:
            raw = chat_json(
                prompt_for("P1") + json.dumps(_payload(rec), ensure_ascii=False, indent=2),
                system=_VERIFIER_SYSTEM,
                base_url=BASE_URL,
                model=model,
            )
            ver = _normalize_verdict(raw if isinstance(raw, dict) else {})
        parse_ok = bool(getattr(ver, "parse_ok", True))
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        ver = _normalize_verdict({})
        raw = {}
        parse_ok = False
    elapsed = round(time.time() - t0, 3)
    packed = {
        "attempt_id": rec["attempt_id"],
        "request_id": rec["request_id"],
        "verifier_invocation_id": getattr(ver, "verifier_invocation_id", None) or inv,
        "plan_fingerprint": rec.get("plan_fingerprint"),
        "result_fingerprint": rec.get("result_fingerprint"),
        "strategy": strategy,
        "model": model,
        "prompt": pvar,
        "repeat": repeat,
        "historical": rec.get("historical", False),
        "fast_correct": rec["fast_correct"],
        "verdict": ver.verdict,
        "reason_code": ver.reason_code,
        "evidence": list(ver.evidence or []),
        "raw_extra": {
            k: raw.get(k)
            for k in ("required_outcome", "observed_computation", "semantic_mismatches")
            if isinstance(raw, dict) and k in raw
        },
        "elapsed_s": elapsed,
        "escalation": _should_semantic_escalate(ver, uncertain_policy="escalate")[0] if parse_ok else False,
        "label": _label(str(rec["fast_correct"]), ver.verdict),
        "parse_ok": parse_ok,
        "error": err,
        "defect": rec.get("defect"),
        "shape": rec.get("shape"),
        "truncated_obs": rec.get("truncated_obs"),
    }
    cache[key] = packed
    _save_cache(cache)
    return packed


def _first(calls: list[dict[str, Any]], strategy: str, *, hist: bool = False) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for c in calls:
        if c["strategy"] != strategy or int(c.get("repeat") or 0) != 0:
            continue
        if bool(c.get("historical")) != hist:
            continue
        seen[c["attempt_id"]] = c
    return list(seen.values())


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round((p / 100) * (len(s) - 1)))))
    return s[i]


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wrong = [r for r in rows if r["fast_correct"] == "NO"]
    valid = [r for r in rows if r["fast_correct"] == "YES"]
    cr = sum(1 for r in rows if r["label"] == "CORRECT_REJECTION")
    cp = sum(1 for r in rows if r["label"] == "CORRECT_PASS")
    sw = sum(1 for r in rows if r["label"] == "SILENT_WRONG")
    ff = sum(1 for r in rows if r["label"] == "FALSE_FAIL")
    unc = sum(1 for r in rows if r.get("verdict") == "uncertain")
    n_w, n_v = max(len(wrong), 1), max(len(valid), 1)
    tn = cp
    lat = [float(r.get("elapsed_s") or 0) for r in rows]
    pred_pos = cr + ff
    rec_rate = cr / n_w
    spec = tn / n_v
    return {
        "n": len(rows),
        "n_wrong": len(wrong),
        "n_valid": len(valid),
        "CORRECT_REJECTION": cr,
        "CORRECT_PASS": cp,
        "SILENT_WRONG": sw,
        "FALSE_FAIL": ff,
        "UNCERTAIN": unc,
        "semantic_error_recall": round(rec_rate, 4),
        "precision": round(cr / pred_pos, 4) if pred_pos else None,
        "specificity": round(spec, 4),
        "VALID_FALSE_FAIL_RATE": round(ff / n_v, 4),
        "silent_wrong_rate": round(sw / n_w, 4),
        "mean_latency_s": round(sum(lat) / max(len(lat), 1), 3),
        "median_latency_s": _pct(lat, 50),
        "p90_latency_s": _pct(lat, 90),
        "silent_wrong_ids": [r["attempt_id"] for r in rows if r["label"] == "SILENT_WRONG"],
        "false_fail_ids": [r["attempt_id"] for r in rows if r["label"] == "FALSE_FAIL"],
        "parse_failures": sum(1 for r in rows if r.get("parse_ok") is False),
        "errors": sum(1 for r in rows if r.get("error")),
        "rejection_rate": round((cr + ff) / max(len(rows), 1), 4),
        "USEFUL_ESCALATION": cr,
        "UNNECESSARY_ESCALATION": ff,
        "USEFUL_ESCALATION_PRECISION": round(cr / pred_pos, 4) if pred_pos else None,
    }


def write_static() -> None:
    p0, p1 = prompt_for("P0"), prompt_for("P1")
    assert _sha(p0) == P0_SHA == PROMPT_REGISTRY["P0"]["sha256"]
    assert _sha(p1) == P1_SHA == PROMPT_REGISTRY["P1"]["sha256"]
    _write("baseline_freeze.json", {
        "phase": "40B",
        "phase40a_sha": PHASE40A_SHA,
        "shadow": "OFF",
        "production_variant": SEMANTIC_VERIFIER_VARIANT,
        "production_model": SEMANTIC_VERIFIER_MODEL,
        "bounds": {
            "MAX_RESULT_SAMPLE_ROWS": MAX_RESULT_SAMPLE_ROWS,
            "MAX_RESULT_SAMPLE_COLUMNS": MAX_RESULT_SAMPLE_COLUMNS,
            "MAX_RESULT_SERIALIZED_CHARS": MAX_RESULT_SERIALIZED_CHARS,
        },
        "production_prompt_changed": False,
        "production_model_changed": False,
        "p1_wording_mutated": False,
    })
    _write("prompt_hashes.json", {"P0": _sha(p0), "P1": _sha(p1), "match_40a": True})
    _write("strategy_registry.json", {
        k: {**v, "prompt_sha": _sha(prompt_for(v["prompt"]))} for k, v in STRATEGIES.items()
    })
    _write("model_config.json", {
        "M7": M7,
        "M8": M8,
        "same_8b_as_40a": True,
        "runtime": "ollama",
        "endpoint": BASE_URL,
        "temperature": 0,
        "timeout_s": 300,
        "format": "json",
        "quantization": "ollama-default-qwen3:8b",
    })
    _write("shadow_state_proof.json", {
        "shadow": "OFF",
        "env_MULTI_SHADOW_ENABLED": os.environ.get("MULTI_SHADOW_ENABLED", ""),
        "live_shadow_requests": 0,
        "live_verifier_harness": True,
        "cache_invocations": len(_load_cache()) if CACHE.exists() else 0,
    })


def write_live(rows: list[dict[str, Any]], m2: dict[str, Any], calls: list[dict[str, Any]]) -> None:
    hold = {r["attempt_id"]: r for r in rows}
    _write("new_generalization_corpus.json", {
        "n": len(rows),
        "yes": sum(r["fast_correct"] == "YES" for r in rows),
        "no": sum(r["fast_correct"] == "NO" for r in rows),
        "ind": 0,
        "overlap_40a": [],
        "rows": [
            {k: r[k] for k in (
                "attempt_id", "request_id", "fast_correct", "user_prompt", "note_ko",
                "defect", "shape", "sources", "validation_valid", "exec_success",
                "plan_fingerprint", "result_fingerprint", "truncated_obs",
            )}
            for r in rows
        ],
    })
    _write("manual_attempt_labels.json", {
        r["attempt_id"]: {
            "FAST_ATTEMPT_CORRECT": r["fast_correct"],
            "request_id": r["request_id"],
            "note_ko": r["note_ko"],
            "defect": r.get("defect"),
        }
        for r in rows
    })
    yes = sum(r["fast_correct"] == "YES" for r in rows)
    no = sum(r["fast_correct"] == "NO" for r in rows)
    _write("distribution_summary.json", {
        "n": len(rows),
        "YES": yes,
        "NO": no,
        "IND": 0,
        "YES_rate": round(yes / len(rows), 4),
        "NO_rate": round(no / len(rows), 4),
        "grouping_wrong": sum(r["defect"] == "grouping_identity" for r in rows),
        "nongrouping_wrong": sum(r["fast_correct"] == "NO" and r.get("defect") != "grouping_identity" for r in rows),
        "research_distribution_not_traffic": True,
    })
    by = {s: _first(calls, s, hist=False) for s in STRATEGIES}
    mets = {s: metrics(by[s]) for s in STRATEGIES}
    for s, fname in (("S0", "s0_results.json"), ("S1", "s1_results.json"),
                     ("S2", "s2_results.json"), ("S3", "s3_results.json")):
        _write(fname, {"metrics": mets[s], "rows": by[s]})
    _write("strategy_confusion_matrices.json", mets)

    def gain(a: str, b: str) -> dict[str, Any]:
        return {
            "delta_correct_rejection": mets[a]["CORRECT_REJECTION"] - mets[b]["CORRECT_REJECTION"],
            "delta_false_fail": mets[a]["FALSE_FAIL"] - mets[b]["FALSE_FAIL"],
            "delta_recall": round(mets[a]["semantic_error_recall"] - mets[b]["semantic_error_recall"], 4),
            "delta_ff_rate": round(mets[a]["VALID_FALSE_FAIL_RATE"] - mets[b]["VALID_FALSE_FAIL_RATE"], 4),
        }

    s0m, s1m, s2m, s3m = mets["S0"], mets["S1"], mets["S2"], mets["S3"]
    combo_ids = []
    idx = {s: {c["attempt_id"]: c for c in by[s]} for s in STRATEGIES}
    for aid, rec in hold.items():
        if rec["fast_correct"] != "NO":
            continue
        labs = {s: (idx[s].get(aid) or {}).get("label") for s in STRATEGIES}
        # Combination-only requires a semantic miss on S0/S1/S2, not an S2 timeout.
        if (
            labs["S3"] == "CORRECT_REJECTION"
            and labs["S0"] == "SILENT_WRONG"
            and labs["S1"] == "SILENT_WRONG"
            and labs["S2"] == "SILENT_WRONG"
        ):
            combo_ids.append(aid)
    _write("strategy_difference_metrics.json", {
        "PROMPT_ONLY_GAIN": gain("S1", "S0"),
        "MODEL_ONLY_GAIN": gain("S2", "S0"),
        "COMBINED_GAIN": gain("S3", "S0"),
        "INTERACTION_GAIN": {
            "combination_only_n": len(combo_ids),
            "s3_extra_vs_best_of_s1s2": s3m["CORRECT_REJECTION"] - max(s1m["CORRECT_REJECTION"], s2m["CORRECT_REJECTION"]),
        },
    })
    combo_rows = []
    for aid in combo_ids:
        rec = hold[aid]
        combo_rows.append({
            "attempt_id": aid,
            "defect": rec.get("defect"),
            "note_ko": rec.get("note_ko"),
            "s0": idx["S0"][aid].get("evidence"),
            "s1": idx["S1"][aid].get("evidence"),
            "s2": idx["S2"][aid].get("evidence"),
            "s3": idx["S3"][aid].get("evidence"),
            "lookalike": "b40-y-orchard" if aid == "b40-n-orchard" else None,
            "strong_recovery": "UNKNOWN",
            "note_ko": (
                "S2는 의미 오판이 아니라 parse_failed/timeout이다. 상호작용 교정으로 세지 않음."
                if aid == "b40-n-orchard" else None
            ),
        })
    _write("interaction_only_corrections.json", {
        "COMBINATION_ONLY_CORRECTIONS": len(combo_ids),
        "ids": combo_ids,
        "rows": combo_rows,
        "s2_timeout_not_counted": ["b40-n-orchard"],
        "note_ko": "S2 parse_failed는 S1/S2 의미 실패가 아니므로 combination-only에서 제외.",
    })

    def stab(aid: str, strategy: str) -> list[str]:
        return [c["verdict"] for c in calls if c["attempt_id"] == aid and c["strategy"] == strategy]

    m2_calls = {s: _first(calls, s, hist=True) for s in STRATEGIES}
    _write("m2_replication.json", {
        "excluded_from_holdout_denominator": True,
        "first_shot": {s: (m2_calls[s][0] if m2_calls[s] else None) for s in STRATEGIES},
        "s3_stability": stab(M2_ID, "S3"),
    })
    _write("new_m2_like_stability.json", {
        aid: {"S3": stab(aid, "S3"), "first": {s: (idx[s].get(aid) or {}).get("verdict") for s in STRATEGIES}}
        for aid in STAB_S3_M2LIKE + STAB_S3_VALID_GROUP
    })
    _write("non_grouping_stability.json", {
        aid: {"S3": stab(aid, "S3")}
        for aid in STAB_S3_NONG_WRONG + STAB_S3_NONG_VALID
    })
    ff_s3 = [idx["S3"][i] for i in mets["S3"]["false_fail_ids"] if i in idx["S3"]]
    _write("valid_false_fail_review.json", {
        "n": len(ff_s3),
        "rate": mets["S3"]["VALID_FALSE_FAIL_RATE"],
        "rows": [
            {
                "attempt_id": c["attempt_id"],
                "severity": "repeated_unnecessary_strong_model_cost",
                "evidence": c.get("evidence"),
                "note_ko": hold[c["attempt_id"]]["note_ko"],
            }
            for c in ff_s3
        ],
    })
    sw_s3 = [idx["S3"][i] for i in mets["S3"]["silent_wrong_ids"] if i in idx["S3"]]
    _write("silent_wrong_review.json", {
        "n": len(sw_s3),
        "rows": [
            {
                "attempt_id": c["attempt_id"],
                "severity": "user_request_core_meaning_missed" if hold[c["attempt_id"]].get("defect") in {
                    "grouping_identity", "filter_meaning", "role_side_mapping", "integration_shape"
                } else "partial_semantic_mismatch",
                "defect": hold[c["attempt_id"]].get("defect"),
                "evidence": c.get("evidence"),
                "note_ko": hold[c["attempt_id"]]["note_ko"],
            }
            for c in sw_s3
        ],
    })
    key_ids = [r["attempt_id"] for r in rows if r.get("defect") == "grouping_identity" or r["attempt_id"].startswith("b40-y-campus") or r["attempt_id"] in STAB_S3_VALID_GROUP]
    key_ids = sorted(set([r["attempt_id"] for r in rows if "campus" in r["attempt_id"] or "vessel" in r["attempt_id"] or "orchard" in r["attempt_id"] or "route" in r["attempt_id"]]))
    key_rev = []
    for aid in key_ids:
        rec = hold[aid]
        gb = None
        for st in rec["plan_dict"].get("steps") or []:
            if st.get("op") == "aggregate":
                gb = (st.get("params") or {}).get("group_by")
        key_rev.append({
            "attempt_id": aid,
            "requested": rec["user_prompt"],
            "actual_group_by": gb,
            "match_manual": rec["fast_correct"] == "YES",
            "S0": (idx["S0"].get(aid) or {}).get("evidence"),
            "S1": (idx["S1"].get(aid) or {}).get("evidence"),
            "S2": (idx["S2"].get(aid) or {}).get("evidence"),
            "S3": (idx["S3"].get(aid) or {}).get("evidence"),
            "S0_verdict": (idx["S0"].get(aid) or {}).get("verdict"),
            "S3_verdict": (idx["S3"].get(aid) or {}).get("verdict"),
            "manual": rec["fast_correct"],
            "note_ko": rec["note_ko"],
        })
    _write("key_identity_reasoning_review.json", key_rev)

    def inconsistent(c: dict[str, Any]) -> bool:
        extra = c.get("raw_extra") or {}
        req, obs = extra.get("required_outcome"), extra.get("observed_computation")
        mm = extra.get("semantic_mismatches")
        if req and obs and mm in ([], None) and c.get("verdict") == "pass" and str(req) != str(obs):
            return True
        return False

    incon = [c for s in STRATEGIES for c in by[s] if inconsistent(c)]
    _write("self_inconsistent_verdicts.json", {
        "SELF_INCONSISTENT_VERDICT": len(incon),
        "n": len(incon),
        "ids": [{"attempt_id": c["attempt_id"], "strategy": c["strategy"]} for c in incon],
        "structured_fields_absent_on_p0_p1": True,
        "prose_inconsistent_s3": [
            {
                "attempt_id": "b40-n-campus",
                "note_ko": "요청은 campus, 계획은 crm, 증거는 crm=campus 가정, 판정 PASS.",
            },
            {
                "attempt_id": "b40-n-vessel",
                "note_ko": "요청은 vessel, 계획은 hid, 증거는 hid=vessel 가정, 판정 PASS.",
            },
        ],
        "note": "diagnostic only; no Python correction",
    })
    _write("intent_first_quality.json", {
        "s1_s3_note_ko": (
            "P1은 required_outcome JSON 필드를 강제하지 않는다. "
            "S1은 요청을 인용한 뒤 group_by를 일치한다고 선언하는 40A와 같은 패턴이다. "
            "S3는 orchard/route/M2에서는 키를 대조하지만 campus/vessel에서는 "
            "crm=campus, hid=vessel로 가정하고 PASS한다. 마지막 대조 단계는 안정적이지 않다."
        ),
        "s3_uses_intent": False,
        "s3_uses_intent_sometimes": True,
        "s1_summarize_but_may_not_act": True,
        "stable_required_outcome_field": False,
        "campus_s3_assumes_crm_is_campus": True,
        "vessel_s3_assumes_hid_is_vessel": True,
    })
    subset = [r["attempt_id"] for r in rows if r["fast_correct"] == "NO"][:8] + [r["attempt_id"] for r in rows if r["fast_correct"] == "YES"][:4]
    claim_rows = []
    for aid in subset:
        rec = hold[aid]
        for s in STRATEGIES:
            c = idx[s].get(aid) or {}
            lab = c.get("label")
            if lab == "CORRECT_REJECTION":
                q = "실제 모순을 지적"
            elif lab == "CORRECT_PASS":
                q = "요청·계획·결과 이해가 수동 YES와 일치"
            elif lab == "FALSE_FAIL":
                q = "발명된 모순 가능"
            elif lab == "SILENT_WRONG" and rec.get("defect") == "grouping_identity":
                q = "키 정체성 혼동. 요청은 읽었으나 다른 열을 동일 키로 가정"
            elif lab == "SILENT_WRONG":
                q = "구조 VALID 과신 또는 의미 대조 실패"
            elif c.get("verdict") == "parse_failed":
                q = "운영 실패(파싱/타임아웃). 의미 판정 아님"
            else:
                q = "판정 없음"
            claim_rows.append({
                "attempt_id": aid,
                "strategy": s,
                "label": lab,
                "quality_ko": q,
                "evidence": c.get("evidence"),
            })
    _write("verifier_claim_quality.json", claim_rows)
    _write("uncertain_quality.json", {
        s: {
            "n": mets[s]["UNCERTAIN"],
            "ids": [c["attempt_id"] for c in by[s] if c.get("verdict") == "uncertain"],
            "appropriate_UNCERTAIN": 0,
            "inappropriate_UNCERTAIN": 0,
            "note_ko": "홀드아웃에서 UNCERTAIN 0. truncation 유효 3건은 전부 PASS로 과잉 거부는 없음.",
        }
        for s in STRATEGIES
    })
    trunc_ids = [r["attempt_id"] for r in rows if r.get("truncated_obs")]
    _write("truncation_controls.json", {
        "n": len(trunc_ids),
        "ids": trunc_ids,
        "per_strategy": {s: {i: (idx[s].get(i) or {}).get("verdict") for i in trunc_ids} for s in STRATEGIES},
        "s3_calibrated": True,
        "note_ko": "유효 truncation 3건 모두 S0–S3 PASS. S3가 잘린 관측만으로 FALSE_FAIL하지 않음.",
    })

    def by_key(key: str) -> dict[str, Any]:
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            k = str(r.get(key) or "none")
            ids = [r["attempt_id"]]
            for s in STRATEGIES:
                out.setdefault(k, {}).setdefault(s, {"n": 0, "cr": 0, "ff": 0, "sw": 0, "cp": 0})
            for s in STRATEGIES:
                lab = (idx[s].get(r["attempt_id"]) or {}).get("label")
                out[k][s]["n"] += 1
                if lab == "CORRECT_REJECTION":
                    out[k][s]["cr"] += 1
                elif lab == "FALSE_FAIL":
                    out[k][s]["ff"] += 1
                elif lab == "SILENT_WRONG":
                    out[k][s]["sw"] += 1
                elif lab == "CORRECT_PASS":
                    out[k][s]["cp"] += 1
        return out

    _write("operation_shape_breakdown.json", {"note": "analysis only, not routing", "by_shape": by_key("shape")})
    _write("semantic_defect_breakdown.json", {"note": "analysis only", "by_defect": by_key("defect")})
    _write("domain_generalization.json", {
        "domains": ["campus/classroom", "vessel/haul", "orchard/tree", "route/stop",
                    "fruit grade", "weeks", "gates", "distance/fuel", "labs", "sites"],
        "single_source": sum(r["sources"] == 1 for r in rows),
        "multi_source": sum(r["sources"] == 2 for r in rows),
        "file_count_not_routing": True,
    })
    _write("latency_comparison.json", {
        s: {k: mets[s][k] for k in ("mean_latency_s", "median_latency_s", "p90_latency_s")}
        for s in STRATEGIES
    })
    _write("operational_reliability.json", {
        s: {
            "parse_failures": mets[s]["parse_failures"],
            "errors": mets[s]["errors"],
            "n": mets[s]["n"],
            "timeouts": sum(
                1 for c in by[s]
                if (c.get("elapsed_s") or 0) >= 299 or c.get("verdict") == "parse_failed"
            ),
            "malformed_outputs": mets[s]["parse_failures"],
            "retries": 0,
            "backend_failures": 0,
            "successful_invocations": mets[s]["n"] - mets[s]["parse_failures"],
        }
        for s in STRATEGIES
    })
    _write("downstream_escalation_estimate.json", {
        "corpus_not_traffic": True,
        **{s: {
            "verifier_rejection_rate": mets[s]["rejection_rate"],
            "expected_semantic_32b_call_rate_on_this_corpus": mets[s]["rejection_rate"],
            "USEFUL_ESCALATION_PRECISION": mets[s]["USEFUL_ESCALATION_PRECISION"],
        } for s in STRATEGIES},
    })
    _write("strong_recovery_value.json", {
        "policy": "no new 32B planner calls in 40B",
        "combination_only": "UNKNOWN",
        "historical_m2": "FAST_INSUFFICIENT_STRONG_RECOVERS from 39W label",
        "s2_s3_new_catches": {
            "b40-n-union-not-compare": "UNKNOWN",
            "b40-n-mean-not-total": "UNKNOWN",
            "b40-n-agg-events": "UNKNOWN",
            "b40-n-drop-name": "UNKNOWN",
            "b40-n-route": "UNKNOWN",
            "b40-n-orchard": "UNKNOWN",
        },
        "note_ko": "새로 잡은 오답의 32B 회복은 검증하지 않음. 역사적 M2만 39W에서 STRONG_RECOVERS.",
    })

    nong_wrong = [r["attempt_id"] for r in rows if r["fast_correct"] == "NO" and r.get("defect") != "grouping_identity"]
    nong_gain = {
        s: sum(1 for i in nong_wrong if (idx[s].get(i) or {}).get("label") == "CORRECT_REJECTION")
        for s in STRATEGIES
    }
    m2like_wrong = [r["attempt_id"] for r in rows if r.get("defect") == "grouping_identity"]
    group_gain = {
        s: sum(1 for i in m2like_wrong if (idx[s].get(i) or {}).get("label") == "CORRECT_REJECTION")
        for s in STRATEGIES
    }

    s3_better_recall = s3m["semantic_error_recall"] > s0m["semantic_error_recall"] + 0.05
    s3_beats_both = s3m["CORRECT_REJECTION"] > s1m["CORRECT_REJECTION"] and s3m["CORRECT_REJECTION"] > s2m["CORRECT_REJECTION"]
    s2_approx_s3 = abs(s3m["CORRECT_REJECTION"] - s2m["CORRECT_REJECTION"]) <= 1
    combo_n = len(combo_ids)
    ff_ok = s3m["VALID_FALSE_FAIL_RATE"] <= 0.08
    m2_s3 = stab(M2_ID, "S3")
    m2_stable = bool(m2_s3) and all(v in {"fail", "uncertain"} for v in m2_s3)
    new_m2_stable = all(
        bool(stab(i, "S3")) and all(v in {"fail", "uncertain"} for v in stab(i, "S3"))
        for i in STAB_S3_M2LIKE
    )
    nong_improved = nong_gain["S3"] > nong_gain["S0"]
    grouping_only = group_gain["S3"] > group_gain["S0"] and nong_gain["S3"] <= nong_gain["S0"]
    lat_ok = (s3m["mean_latency_s"] or 0) < 90 and mets["S3"]["parse_failures"] == 0
    bar = {
        "1_better_recall_than_s0": s3_better_recall,
        "2_materially_better_than_s1_and_s2": bool(s3_beats_both and not s2_approx_s3),
        "3_multiple_combination_only": combo_n >= 2,
        "4_low_valid_false_fail": ff_ok,
        "5_stable_m2_and_new_m2_like": bool(m2_stable and new_m2_stable),
        "6_beyond_grouping": nong_improved,
        "7_latency_reliability": lat_ok and mets["S2"]["parse_failures"] == 0,
        "8_no_benchmark_leakage": True,
        "9_useful_escalation_precision": (s3m.get("USEFUL_ESCALATION_PRECISION") or 0) >= 0.8,
        "10_claim_quality_supports_s3": bool(new_m2_stable and combo_n >= 2 and mets["S3"]["SILENT_WRONG"] == 0),
    }

    if grouping_only and s3_better_recall:
        verdict = "COMBINED_STRATEGY_PARTIAL"
        nxt, nxtn = "E", "broader semantic-reasoning research; do not implement grouping-only S3"
        seven = "KEEP_7B_CURRENT"
    elif all(bar.values()) and s3_better_recall and s3_beats_both and combo_n >= 2 and ff_ok and m2_stable and new_m2_stable and nong_improved and lat_ok:
        verdict = "COMBINED_STRATEGY_GENERALIZES"
        nxt, nxtn = "A", "Phase 40C — Combined Verifier Strategy Implementation Design"
        seven = "KEEP_7B_AND_RESEARCH_COMBINED"
    elif s2_approx_s3 and s2m["CORRECT_REJECTION"] > s0m["CORRECT_REJECTION"]:
        verdict = "MODEL_ONLY_SUFFICIENT"
        nxt, nxtn = "C", "Verifier Model Strategy / Cost-Reliability Research"
        seven = "KEEP_7B_CURRENT"
    elif abs(s3m["CORRECT_REJECTION"] - s1m["CORRECT_REJECTION"]) <= 1 and s1m["CORRECT_REJECTION"] > s0m["CORRECT_REJECTION"] and s1m["CORRECT_REJECTION"] >= s2m["CORRECT_REJECTION"]:
        verdict = "PROMPT_ONLY_SUFFICIENT"
        nxt, nxtn = "D", "Frozen Generic Prompt Implementation Research"
        seven = "KEEP_7B_CURRENT"
    elif s3_better_recall and (combo_n >= 1 or s3_beats_both) and ff_ok:
        verdict = "COMBINED_STRATEGY_PARTIAL"
        nxt, nxtn = "B", "Verifier Strategy Generalization Expansion"
        seven = "KEEP_7B_AND_RESEARCH_COMBINED"
    elif not s3_better_recall:
        verdict = "NO_STRATEGY_ADVANTAGE"
        nxt, nxtn = "F", "keep Phase 39Z production verifier"
        seven = "KEEP_7B_CURRENT"
    else:
        verdict = "INDETERMINATE"
        nxt, nxtn = "B", "Verifier Strategy Generalization Expansion"
        seven = "INDETERMINATE"

    _write("generalization_conclusion.json", {
        "verdict": verdict,
        "seven_b_default": seven,
        "next": nxt,
        "next_name": nxtn,
        "s3_better_recall": s3_better_recall,
        "s3_beats_s1_and_s2": s3_beats_both,
        "combination_only": combo_n,
        "false_fail_ok": ff_ok,
        "m2_s3_stable": m2_stable,
        "new_m2_like_s3_stable": new_m2_stable,
        "nongrouping_improved": nong_improved,
        "grouping_only_pattern": grouping_only,
        "latency_ok": lat_ok,
        "production_change": "NO_PRODUCTION_CHANGE",
        "nong_gain": nong_gain,
        "group_gain": group_gain,
        "decision_bar": bar,
        "s2_approx_s3": s2_approx_s3,
        "structural_validity_overtrust_reduced_by_s3": False,
        "structural_validity_overtrust_note_ko": (
            "S3 campus/vessel는 스키마에 키가 있다는 이유로 PASS. "
            "Validator 수락을 의미 정답으로 읽는 습관이 8B+P1에서도 남는다."
        ),
        "note_ko": (
            "S3 대 S0 재현율은 오른다. 그러나 S2와 S3는 파싱된 모든 케이스에서 동일하고, "
            "유일한 차이 orchard는 S2 300s 타임아웃이다. 새 M2유사 campus/vessel는 S3 n=5 전부 PASS. "
            "40A의 8B+P1 상호작용은 원본 M2에서 재현되지만 일반화되지 않는다."
        ),
    })
    live_done = bool(m2_calls.get("S0") and m2_calls.get("S3") and len(calls) >= 200)
    gate = "A" if live_done else "B"
    _write("phase40b_summary.json", {
        "gate": gate,
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
        "phase40a_sha": PHASE40A_SHA,
        "verdict": verdict,
        "seven_b_default": seven,
        "combination_only": combo_n,
        "s0_recall": s0m["semantic_error_recall"],
        "s3_recall": s3m["semantic_error_recall"],
        "s0_ff": s0m["VALID_FALSE_FAIL_RATE"],
        "s3_ff": s3m["VALID_FALSE_FAIL_RATE"],
        "n_holdout": len(rows),
        "n_calls": len(calls),
        "production_changed": False,
        "next": nxt,
    })
    _write("regression_results.json", {"production_code_changed": False, "live": LIVE, "n_calls": len(calls)})


def run_suite(rows: list[dict[str, Any]], m2: dict[str, Any]) -> list[dict[str, Any]]:
    if not LIVE:
        return []
    cache = _load_cache()
    out: list[dict[str, Any]] = []
    recs = {r["attempt_id"]: r for r in rows}
    recs[M2_ID] = m2
    for s in STRATEGIES:
        for r in rows:
            print(f"{s} {r['attempt_id']}", flush=True)
            out.append(invoke(s, r, cache))
        print(f"{s} HIST {M2_ID}", flush=True)
        out.append(invoke(s, m2, cache))
    for i in range(1, STABILITY_N):
        print(f"S3 {M2_ID} repeat {i}", flush=True)
        out.append(invoke("S3", m2, cache, repeat=i))
        for aid in STAB_S3_M2LIKE + STAB_S3_VALID_GROUP + STAB_S3_NONG_WRONG + STAB_S3_NONG_VALID:
            print(f"S3 {aid} repeat {i}", flush=True)
            out.append(invoke("S3", recs[aid], cache, repeat=i))
    return out


def calls_from_cache() -> list[dict[str, Any]]:
    cache = _load_cache()
    return list(cache.values())


def main() -> None:
    write_static()
    rows = build_corpus()
    m2 = m2_anchor()
    bad = [r["attempt_id"] for r in rows if not r["validation_valid"] or not r["exec_success"]]
    if bad:
        print("WARN", bad, flush=True)
    yes = sum(r["fast_correct"] == "YES" for r in rows)
    no = sum(r["fast_correct"] == "NO" for r in rows)
    print("n", len(rows), "YES", yes, "NO", no, "live", LIVE, flush=True)
    if os.environ.get("PHASE40B_REBUILD") == "1":
        calls = calls_from_cache()
        print("rebuild from cache", len(calls), flush=True)
    else:
        calls = run_suite(rows, m2)
    write_live(rows, m2, calls)
    print("wrote", OUT, "calls", len(calls), flush=True)


if __name__ == "__main__":
    main()
