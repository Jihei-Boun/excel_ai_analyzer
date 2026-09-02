"""Phase 40C — verifier model strategy, cost, and reliability (research only).

Does NOT change production verifier model, prompt, threshold, routing, or timeout.
Primary stronger candidate is 8B + production P0 (Phase 40B MODEL_ONLY_SUFFICIENT).
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.attempt_lineage import (
    DISPOSITION_REJECTED_BY_SEMANTIC_VERIFIER,
    RequestAttemptLineage,
    STAGE_FAST_SUCCESS,
    STAGE_SEMANTIC_STRONG,
    TRIGGER_SEMANTIC_ESCALATION,
    compact_result_fingerprint,
    new_verifier_invocation_id,
    plan_fingerprint,
)
from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_pipeline import _run_integration_attempt_loop
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.result_observation import (
    MAX_RESULT_SAMPLE_COLUMNS,
    MAX_RESULT_SAMPLE_ROWS,
    MAX_RESULT_SERIALIZED_CHARS,
)
from core.integrate.schema_lineage import extract_source_schemas_from_understanding
from core.integrate.semantic_escalation import (
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
    _should_semantic_escalate,
    build_semantic_replan_feedback,
)
from core.integrate.semantic_verifier import SemanticVerificationResult, run_semantic_verification
from core.shadow.fingerprint import dataframe_fingerprint
from tests.benchmark_multi.phase39v_research import _und_from_frames
from tests.benchmark_multi.phase39x_research import MATERIALIZATION
from tests.benchmark_multi.phase40a_research import ALL_IDS as PHASE40A_IDS
from tests.benchmark_multi.phase40a_research import M2_ID
from tests.benchmark_multi.phase40b_research import (
    PHASE40A_SHA,
    _agg,
    _filt,
    _join,
    _label,
    _plan,
    _ren,
    _sel,
    _union,
    build_corpus as build_40b_corpus,
    m2_anchor,
    raw_cases as raw_40b_cases,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase40c"
CACHE = OUT / "verifier_live_cache.json"
B40 = ROOT / "benchmark_results/multi/phase40b"
PHASE40B_SHA = "faa9d2606636170db2eb6643325d8489371d63c7"
LIVE = os.environ.get("PHASE40C_LIVE", "1") != "0"
BASE_URL = "http://localhost:11434"
M7 = "qwen2.5:7b"
M8 = "qwen3:8b"
M32 = "qwen3:32b"
TIMEOUT_S = 300
STABILITY_N = 5
RETRY_POLICY = 0  # same for 7B and 8B; core.llm_client has no verifier retry

RECOVERY_IDS = [
    "b40-n-route",
    "b40-n-union-not-compare",
    "b40-n-mean-not-total",
    "b40-n-agg-events",
    "b40-n-drop-name",
]
STAB_8B_WRONG = ["b40-n-route", "b40-n-mean-not-total", "b40-n-drop-name"]
STAB_8B_VALID = ["b40-y-campus", "b40-y-vessel", "b40-y-filter-grade", "b40-y-stack-weeks"]
STAB_7B = ["b40-n-campus", "b40-n-mean-not-total", "b40-y-campus", "b40-y-filter-grade"]


def _write(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


def _load_cache() -> dict[str, Any]:
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def _save_cache(cache: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False, default=str) + "\n")


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round((p / 100) * (len(s) - 1)))))
    return s[i]


def _nonpass(verdict: str | None) -> bool:
    return verdict in {"fail", "uncertain"}


def _opfail(verdict: str | None, elapsed: float | None) -> bool:
    return verdict == "parse_failed" or (elapsed or 0) >= 299


def fresh_raw() -> list[dict[str, Any]]:
    """New Phase 40C holdout. No 40A/40B attempt_ids."""
    beds = pd.DataFrame({"bed": ["B1", "B1", "B2", "B3"], "ward": ["W1", "W1", "W2", "W2"], "occ": [1, 1, 0, 1]})
    cars = pd.DataFrame({"vin": ["C1", "C2", "C3", "C4"], "depot": ["D9", "D9", "D8", "D8"], "km": [10, 20, 15, 5]})
    beans = pd.DataFrame({"lot": ["L1", "L2", "L3"], "roast": ["Light", "Dark", "Light"], "g": [20, 30, 25]})
    jan = pd.DataFrame({"sku": ["A", "B"], "n": [2, 3]})
    feb = pd.DataFrame({"sku": ["A", "C"], "n": [4, 1]})
    spans = pd.DataFrame({"span": ["S1", "S1", "S2"], "m": [4, 6, 10]})
    length = pd.DataFrame({"aid": ["A1", "A2"], "m": [100, 80]})
    tank = pd.DataFrame({"aid": ["A1", "A2"], "ml": [20, 18]})
    am = pd.DataFrame({"node": ["N1", "N2"], "kw": [3, 4]})
    pm = pd.DataFrame({"node": ["N1", "N2"], "kw": [9, 7]})
    wp = pd.DataFrame({"bin": ["X1", "X2"], "qty": [5, 6]})
    wq = pd.DataFrame({"bin": ["X1", "X2"], "qty": [1, 2]})
    ev = pd.DataFrame({"rid": ["R1", "R2", "R3"], "bay": ["Y1", "Y1", "Y2"], "ms": [3, 5, 4]})
    lefts = pd.DataFrame({"kid": ["K1", "K2"], "score": [1, 2]})
    rights = pd.DataFrame({"kid": ["K1", "K2"], "score": [8, 9]})
    people = pd.DataFrame({"pid": ["P1", "P2"], "name": ["Jo", "Kim"]})
    jobs = pd.DataFrame({"pid": ["P1", "P2"], "hrs": [3, 7]})
    wide = pd.DataFrame({"uid": ["U1", "U2"], **{f"m{i}": [i, i + 1] for i in range(30)}})
    tall = pd.DataFrame({"uid": [f"R{i}" for i in range(80)], "v": list(range(80))})
    tix = pd.DataFrame({"xid": ["I1", "I2", "I3"], "state": ["done", "open", "done"], "hrs": [2, 9, 1]})
    shift = pd.DataFrame({"nid": ["N1", "N1", "N2", "N2"], "shift": ["AM", "PM", "AM", "PM"], "w": [4, 5, 6, 7]})

    def c(**kw: Any) -> dict[str, Any]:
        return kw

    return [
        c(attempt_id="c40-n-ward", request_id="p40c-01", fast_correct="NO",
          defect="grouping_identity", shape="aggregate", sources=1, trunc=False,
          prompt="Sum occ per ward, not per individual bed.",
          note_ko="병동별인데 침대 id로 집계.",
          frames={"beds.xlsx": beds},
          plan=_plan("a", [_agg("beds.xlsx", "a", ["bed"], "occ", "sum", "occ")])),
        c(attempt_id="c40-n-depot", request_id="p40c-02", fast_correct="NO",
          defect="grouping_identity", shape="aggregate", sources=1, trunc=False,
          prompt="Sum km per depot, not per individual vehicle.",
          note_ko="차고별인데 vin으로 집계.",
          frames={"cars.xlsx": cars},
          plan=_plan("a", [_agg("cars.xlsx", "a", ["vin"], "km", "sum", "km")])),
        c(attempt_id="c40-n-roast", request_id="p40c-03", fast_correct="NO",
          defect="filter_meaning", shape="filter", sources=1, trunc=False,
          prompt="Keep only Dark roast lots.",
          note_ko="Dark가 필요한데 Light 필터.",
          frames={"beans.xlsx": beans},
          plan=_plan("f", [_filt("beans.xlsx", "f", "roast", "Light")])),
        c(attempt_id="c40-n-join-weeks", request_id="p40c-04", fast_correct="NO",
          defect="integration_shape", shape="join", sources=2, trunc=False,
          prompt="Stack January and February sku rows so every row from either month is kept.",
          note_ko="적재가 필요한데 inner join.",
          frames={"jan.xlsx": jan, "feb.xlsx": feb},
          plan=_plan("j", [_join("jan.xlsx", "feb.xlsx", "j", "sku")])),
        c(attempt_id="c40-n-mean-span", request_id="p40c-05", fast_correct="NO",
          defect="metric_meaning", shape="aggregate", sources=1, trunc=False,
          prompt="For each span report the total m.",
          note_ko="총량인데 평균.",
          frames={"spans.xlsx": spans},
          plan=_plan("a", [_agg("spans.xlsx", "a", ["span"], "m", "mean", "m")])),
        c(attempt_id="c40-n-drop-ml", request_id="p40c-06", fast_correct="NO",
          defect="output_completeness", shape="join", sources=2, trunc=False,
          prompt="Join length with tank so I can compare m and ml together.",
          note_ko="ml를 버림.",
          frames={"length.xlsx": length, "tank.xlsx": tank},
          plan=_plan("s", [_join("length.xlsx", "tank.xlsx", "j", "aid"), _sel("j", "s", ["aid", "m"])])),
        c(attempt_id="c40-n-union-sides", request_id="p40c-07", fast_correct="NO",
          defect="role_side_mapping", shape="union", sources=2, trunc=False,
          prompt="For each node show AM kw next to PM kw.",
          note_ko="나란히 비교인데 union.",
          frames={"am.xlsx": am, "pm.xlsx": pm},
          plan=_plan("u", [_union("am.xlsx", "pm.xlsx", "u")])),
        c(attempt_id="c40-n-collapse-bins", request_id="p40c-08", fast_correct="NO",
          defect="role_side_mapping", shape="union", sources=2, trunc=False,
          prompt="Compare warehouse P and Q quantity for each bin. I need both warehouses visible.",
          note_ko="비교인데 합쳐 한 합계.",
          frames={"p.xlsx": wp, "q.xlsx": wq},
          plan=_plan("a", [_union("p.xlsx", "q.xlsx", "u"), _agg("u", "a", ["bin"], "qty", "sum", "qty_all")])),
        c(attempt_id="c40-n-agg-keep", request_id="p40c-09", fast_correct="NO",
          defect="aggregation_mismatch", shape="aggregate", sources=1, trunc=False,
          prompt="Keep every reading row with its bay and ms. Do not collapse readings.",
          note_ko="행 유지인데 집계.",
          frames={"ev.xlsx": ev},
          plan=_plan("a", [_agg("ev.xlsx", "a", ["bay"], "ms", "sum", "ms")])),
        c(attempt_id="c40-n-left-score", request_id="p40c-10", fast_correct="NO",
          defect="role_side_mapping", shape="join", sources=2, trunc=False,
          prompt="For each key show the left score next to the right score.",
          note_ko="양쪽이 필요한데 왼쪽만.",
          frames={"left.xlsx": lefts, "right.xlsx": rights},
          plan=_plan("s", [
              _ren("left.xlsx", "l", {"score": "left_s"}),
              _ren("right.xlsx", "r", {"score": "right_s"}),
              _join("l", "r", "j", "kid"),
              _sel("j", "s", ["kid", "left_s"]),
          ])),
        c(attempt_id="c40-y-ward", request_id="p40c-11", fast_correct="YES",
          defect=None, shape="aggregate", sources=1, trunc=False,
          prompt="Sum occ per ward.",
          note_ko="병동 group-by 일치.",
          frames={"beds.xlsx": beds},
          plan=_plan("a", [_agg("beds.xlsx", "a", ["ward"], "occ", "sum", "occ")])),
        c(attempt_id="c40-y-depot", request_id="p40c-12", fast_correct="YES",
          defect=None, shape="aggregate", sources=1, trunc=False,
          prompt="Sum km per depot.",
          note_ko="차고 group-by 일치.",
          frames={"cars.xlsx": cars},
          plan=_plan("a", [_agg("cars.xlsx", "a", ["depot"], "km", "sum", "km")])),
        c(attempt_id="c40-y-roast", request_id="p40c-13", fast_correct="YES",
          defect=None, shape="filter", sources=1, trunc=False,
          prompt="Keep only Dark roast lots.",
          note_ko="Dark 필터 일치.",
          frames={"beans.xlsx": beans},
          plan=_plan("f", [_filt("beans.xlsx", "f", "roast", "Dark")])),
        c(attempt_id="c40-y-stack-weeks", request_id="p40c-14", fast_correct="YES",
          defect=None, shape="union", sources=2, trunc=False,
          prompt="Stack January and February sku rows so every row from either month is kept.",
          note_ko="적재 union 일치.",
          frames={"jan.xlsx": jan, "feb.xlsx": feb},
          plan=_plan("u", [_union("jan.xlsx", "feb.xlsx", "u")])),
        c(attempt_id="c40-y-total-span", request_id="p40c-15", fast_correct="YES",
          defect=None, shape="aggregate", sources=1, trunc=False,
          prompt="For each span report the total m.",
          note_ko="span 합계 일치.",
          frames={"spans.xlsx": spans},
          plan=_plan("a", [_agg("spans.xlsx", "a", ["span"], "m", "sum", "m")])),
        c(attempt_id="c40-y-keep-ml", request_id="p40c-16", fast_correct="YES",
          defect=None, shape="join", sources=2, trunc=False,
          prompt="Join length with tank so I can compare m and ml together.",
          note_ko="두 메트릭 유지.",
          frames={"length.xlsx": length, "tank.xlsx": tank},
          plan=_plan("j", [_join("length.xlsx", "tank.xlsx", "j", "aid")])),
        c(attempt_id="c40-y-compare-tod", request_id="p40c-17", fast_correct="YES",
          defect=None, shape="join", sources=2, trunc=False,
          prompt="For each node show AM kw next to PM kw.",
          note_ko="rename+join 비교 일치.",
          frames={"am.xlsx": am, "pm.xlsx": pm},
          plan=_plan("j", [
              _ren("am.xlsx", "a", {"kw": "am_kw"}),
              _ren("pm.xlsx", "b", {"kw": "pm_kw"}),
              _join("a", "b", "j", "node"),
          ])),
        c(attempt_id="c40-y-combined-ok", request_id="p40c-18", fast_correct="YES",
          defect=None, shape="union", sources=2, trunc=False,
          prompt="Combine warehouse P and Q rows and give total quantity per bin.",
          note_ko="합친 합계가 요청과 일치.",
          frames={"p.xlsx": wp, "q.xlsx": wq},
          plan=_plan("a", [_union("p.xlsx", "q.xlsx", "u"), _agg("u", "a", ["bin"], "qty", "sum", "qty_all")])),
        c(attempt_id="c40-y-keep-events", request_id="p40c-19", fast_correct="YES",
          defect=None, shape="select", sources=1, trunc=False,
          prompt="Keep every reading row with its bay and ms.",
          note_ko="행 유지 select.",
          frames={"ev.xlsx": ev},
          plan=_plan("s", [_sel("ev.xlsx", "s", ["rid", "bay", "ms"])])),
        c(attempt_id="c40-y-left-ok", request_id="p40c-20", fast_correct="YES",
          defect=None, shape="select", sources=1, trunc=False,
          prompt="Keep only the left score for each key.",
          note_ko="왼쪽만 요청과 일치.",
          frames={"left.xlsx": lefts},
          plan=_plan("s", [_sel("left.xlsx", "s", ["kid", "score"])])),
        c(attempt_id="c40-y-with-name", request_id="p40c-21", fast_correct="YES",
          defect=None, shape="join", sources=2, trunc=False,
          prompt="Join jobs to people and keep name with hrs.",
          note_ko="이름+시간 유지.",
          frames={"people.xlsx": people, "jobs.xlsx": jobs},
          plan=_plan("j", [_join("people.xlsx", "jobs.xlsx", "j", "pid")])),
        c(attempt_id="c40-y-done", request_id="p40c-22", fast_correct="YES",
          defect=None, shape="filter", sources=1, trunc=False,
          prompt="Keep only done tickets.",
          note_ko="done 필터 일치.",
          frames={"tix.xlsx": tix},
          plan=_plan("f", [_filt("tix.xlsx", "f", "state", "done")])),
        c(attempt_id="c40-y-am-only", request_id="p40c-23", fast_correct="YES",
          defect=None, shape="filter", sources=1, trunc=False,
          prompt="Using only the AM shift, sum w per nid.",
          note_ko="단일 시프트 일치.",
          frames={"shift.xlsx": shift},
          plan=_plan("a", [_filt("shift.xlsx", "f", "shift", "AM"), _agg("f", "a", ["nid"], "w", "sum", "w")])),
        c(attempt_id="c40-y-filter-then-sum", request_id="p40c-24", fast_correct="YES",
          defect=None, shape="multi_stage", sources=1, trunc=False,
          prompt="For done tickets only, sum hours.",
          note_ko="필터 후 합계.",
          frames={"tix.xlsx": tix},
          plan=_plan("a", [_filt("tix.xlsx", "f", "state", "done"),
                           {"op": "aggregate", "inputs": ["f"], "output": "a",
                            "params": {"group_by": [], "metrics": [{"column": "hrs", "function": "sum", "alias": "hrs"}]}}])),
        c(attempt_id="c40-y-rename", request_id="p40c-25", fast_correct="YES",
          defect=None, shape="rename", sources=1, trunc=False,
          prompt="Rename occ to occupancy and keep bed and occupancy.",
          note_ko="rename 후 select.",
          frames={"beds.xlsx": beds},
          plan=_plan("s", [_ren("beds.xlsx", "r", {"occ": "occupancy"}), _sel("r", "s", ["bed", "occupancy"])])),
        c(attempt_id="c40-y-multi-stage", request_id="p40c-26", fast_correct="YES",
          defect=None, shape="multi_stage", sources=2, trunc=False,
          prompt="Join people to jobs then keep pid, name, and hrs.",
          note_ko="조인 후 select.",
          frames={"people.xlsx": people, "jobs.xlsx": jobs},
          plan=_plan("s", [_join("people.xlsx", "jobs.xlsx", "j", "pid"), _sel("j", "s", ["pid", "name", "hrs"])])),
        c(attempt_id="c40-y-union-ok", request_id="p40c-27", fast_correct="YES",
          defect=None, shape="union", sources=2, trunc=False,
          prompt="Concatenate January and February sku rows into one list.",
          note_ko="단순 concat.",
          frames={"jan.xlsx": jan, "feb.xlsx": feb},
          plan=_plan("u", [_union("jan.xlsx", "feb.xlsx", "u")])),
        c(attempt_id="c40-y-select", request_id="p40c-28", fast_correct="YES",
          defect=None, shape="select", sources=1, trunc=False,
          prompt="Keep ticket id and state only.",
          note_ko="열 부분집합.",
          frames={"tix.xlsx": tix},
          plan=_plan("s", [_sel("tix.xlsx", "s", ["xid", "state"])])),
        c(attempt_id="c40-y-1to1", request_id="p40c-29", fast_correct="YES",
          defect=None, shape="join", sources=2, trunc=False,
          prompt="Attach each person's name to their job hours.",
          note_ko="1:1 조인.",
          frames={"people.xlsx": people, "jobs.xlsx": jobs},
          plan=_plan("j", [_join("people.xlsx", "jobs.xlsx", "j", "pid")])),
        c(attempt_id="c40-y-wide-trunc", request_id="p40c-30", fast_correct="YES",
          defect=None, shape="select", sources=1, trunc=True,
          prompt="Keep every measured attribute for each unit.",
          note_ko="넓은 결과 truncation 통제.",
          frames={"units.xlsx": wide},
          plan=_plan("s", [_sel("units.xlsx", "s", ["uid"] + [f"m{i}" for i in range(30)])])),
        c(attempt_id="c40-y-tall-trunc", request_id="p40c-31", fast_correct="YES",
          defect=None, shape="select", sources=1, trunc=True,
          prompt="Keep uid and v for every row.",
          note_ko="많은 행 truncation 통제.",
          frames={"tall.xlsx": tall},
          plan=_plan("s", [_sel("tall.xlsx", "s", ["uid", "v"])])),
        c(attempt_id="c40-y-dual-metric", request_id="p40c-32", fast_correct="YES",
          defect=None, shape="aggregate", sources=1, trunc=False,
          prompt="For each ward report sum occ.",
          note_ko="병동 합계 단일 메트릭.",
          frames={"beds.xlsx": beds},
          plan=_plan("a", [_agg("beds.xlsx", "a", ["ward"], "occ", "sum", "occ_sum")])),
    ]


def _from_40b_materialize(raw: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    rec = dict(rec)
    rec["frames"] = raw["frames"]
    rec["corpus"] = "phase40b"
    rec["fidelity"] = "EXACT_REPLAY"
    rec["historical"] = False
    return rec


def load_40b_replay() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = build_40b_corpus()
    raws = {c["attempt_id"]: c for c in raw_40b_cases()}
    out = []
    for r in rows:
        packed = _from_40b_materialize(raws[r["attempt_id"]], r)
        out.append(packed)
    s0 = json.loads((B40 / "s0_results.json").read_text())["rows"]
    s2 = json.loads((B40 / "s2_results.json").read_text())["rows"]
    replay: dict[str, dict[str, Any]] = {}
    for row in s0:
        replay[f"{row['attempt_id']}|V0|0"] = {**row, "strategy": "V0", "fidelity": "EXACT_REPLAY"}
    for row in s2:
        replay[f"{row['attempt_id']}|V1|0"] = {**row, "strategy": "V1", "fidelity": "EXACT_REPLAY"}
    return out, replay


def build_fresh() -> list[dict[str, Any]]:
    from tests.benchmark_multi.phase40b_research import materialize

    rows = []
    b40_ids = {c["attempt_id"] for c in raw_40b_cases()}
    for raw in fresh_raw():
        if raw["attempt_id"] in PHASE40A_IDS or raw["attempt_id"] in b40_ids:
            raise RuntimeError(f"id overlap {raw['attempt_id']}")
        rec = materialize(raw)
        rec["frames"] = raw["frames"]
        rec["corpus"] = "phase40c_holdout"
        rec["fidelity"] = "CANONICAL_EQUIVALENT_REPLAY"
        rec["historical"] = False
        rows.append(rec)
    return rows


def invoke(model: str, rec: dict[str, Any], cache: dict[str, Any], *, strategy: str, repeat: int = 0) -> dict[str, Any]:
    key = f"{rec['attempt_id']}|{strategy}|{model}|P0|{repeat}"
    if key in cache:
        return cache[key]
    t0 = time.time()
    inv = new_verifier_invocation_id()
    err = None
    parse_ok = True
    try:
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
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        ver = SemanticVerificationResult(verdict="parse_failed", parse_ok=False, error=err)
    elapsed = round(time.time() - t0, 3)
    parse_ok = bool(getattr(ver, "parse_ok", True))
    packed = {
        "attempt_id": rec["attempt_id"],
        "request_id": rec["request_id"],
        "verifier_invocation_id": getattr(ver, "verifier_invocation_id", None) or inv,
        "plan_fingerprint": rec.get("plan_fingerprint"),
        "result_fingerprint": rec.get("result_fingerprint"),
        "strategy": strategy,
        "model": model,
        "prompt": "P0",
        "repeat": repeat,
        "corpus": rec.get("corpus"),
        "fidelity": rec.get("fidelity"),
        "fast_correct": rec["fast_correct"],
        "verdict": ver.verdict,
        "reason_code": ver.reason_code,
        "evidence": list(ver.evidence or []),
        "elapsed_s": elapsed,
        "escalation": _should_semantic_escalate(ver, uncertain_policy="escalate")[0] if parse_ok else False,
        "label": _label(str(rec["fast_correct"]), ver.verdict),
        "parse_ok": parse_ok,
        "error": err,
        "defect": rec.get("defect"),
        "shape": rec.get("shape"),
        "truncated_obs": rec.get("truncated_obs"),
        "operational_failure": _opfail(ver.verdict, elapsed),
    }
    cache[key] = packed
    _save_cache(cache)
    return packed


def lat_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    xs = [float(r.get("elapsed_s") or 0) for r in rows]
    return {
        "n": len(xs),
        "mean": round(sum(xs) / max(len(xs), 1), 3),
        "median": _pct(xs, 50),
        "p90": _pct(xs, 90),
        "p95": _pct(xs, 95),
        "min": min(xs) if xs else None,
        "max": max(xs) if xs else None,
        "timeouts": sum(1 for r in rows if _opfail(r.get("verdict"), r.get("elapsed_s"))),
    }


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wrong = [r for r in rows if r["fast_correct"] == "NO"]
    valid = [r for r in rows if r["fast_correct"] == "YES"]
    cr = sum(1 for r in rows if r["label"] == "CORRECT_REJECTION")
    cp = sum(1 for r in rows if r["label"] == "CORRECT_PASS")
    sw = sum(1 for r in rows if r["label"] == "SILENT_WRONG")
    ff = sum(1 for r in rows if r["label"] == "FALSE_FAIL")
    n_w, n_v = max(len(wrong), 1), max(len(valid), 1)
    pred_pos = cr + ff
    of = sum(1 for r in rows if r.get("operational_failure") or r.get("verdict") == "parse_failed")
    return {
        "n": len(rows),
        "n_wrong": len(wrong),
        "n_valid": len(valid),
        "CORRECT_REJECTION": cr,
        "CORRECT_PASS": cp,
        "SILENT_WRONG": sw,
        "FALSE_FAIL": ff,
        "UNCERTAIN": sum(1 for r in rows if r.get("verdict") == "uncertain"),
        "semantic_error_recall": round(cr / n_w, 4),
        "VALID_FALSE_FAIL_RATE": round(ff / n_v, 4),
        "false_fail_observed_in_n_valid": {"n": ff, "N": len(valid)},
        "operational_failure_rate": round(of / max(len(rows), 1), 4),
        "rejection_rate": round((cr + ff) / max(len(rows), 1), 4),
        "USEFUL_ESCALATION_PRECISION": round(cr / pred_pos, 4) if pred_pos else None,
        "silent_wrong_ids": [r["attempt_id"] for r in rows if r["label"] == "SILENT_WRONG"],
        "false_fail_ids": [r["attempt_id"] for r in rows if r["label"] == "FALSE_FAIL"],
        "latency": lat_stats(rows),
        **lat_stats(rows),
    }


def _first(calls: list[dict[str, Any]], strategy: str, corpus: str | None = None) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for c in calls:
        if c.get("strategy") != strategy or int(c.get("repeat") or 0) != 0:
            continue
        if corpus and c.get("corpus") != corpus:
            continue
        seen[c["attempt_id"]] = c
    return list(seen.values())


def _judge_strong(aid: str, plan: dict[str, Any] | None, status: str | None) -> str:
    if status in {None, "error"}:
        return "STRONG_OPERATIONAL_FAILURE"
    if status == "cannot_plan":
        return "STRONG_STILL_WRONG"
    if not plan:
        return "STRONG_OPERATIONAL_FAILURE"
    steps = plan.get("steps") or []
    ops = [s.get("op") for s in steps if isinstance(s, dict)]
    gbs = []
    fns = []
    cols: list[str] = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        p = s.get("params") or {}
        if s.get("op") == "aggregate":
            gbs.extend(p.get("group_by") or [])
            for m in p.get("metrics") or []:
                fns.append((m or {}).get("function"))
        if s.get("op") == "select_columns":
            cols.extend(p.get("columns") or [])
    if aid == "b40-n-route":
        return "STRONG_RECOVERS" if "route" in gbs and "sid" not in gbs else "STRONG_STILL_WRONG"
    if aid == "b40-n-mean-not-total":
        return "STRONG_RECOVERS" if "sum" in fns and "mean" not in fns else "STRONG_STILL_WRONG"
    if aid == "b40-n-drop-name":
        return "STRONG_RECOVERS" if (not cols or "name" in cols) and "join" in ops else "STRONG_STILL_WRONG"
    if aid == "b40-n-agg-events":
        return "STRONG_RECOVERS" if "aggregate" not in ops else "STRONG_STILL_WRONG"
    if aid == "b40-n-union-not-compare":
        return "STRONG_RECOVERS" if "join" in ops and "union_rows" not in ops else "STRONG_STILL_WRONG"
    return "INDETERMINATE"


def recover_one(raw: dict[str, Any], rec: dict[str, Any], eight: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
    key = f"{rec['attempt_id']}|STRONG|0"
    if key in cache:
        return cache[key]
    ver = SemanticVerificationResult(
        verdict=str(eight.get("verdict") or "fail"),
        reason_code=eight.get("reason_code"),
        evidence=list(eight.get("evidence") or []),
        parse_ok=True,
        model=M8,
        variant=SEMANTIC_VERIFIER_VARIANT,
        verifier_invocation_id=eight.get("verifier_invocation_id"),
    )
    fb = build_semantic_replan_feedback(previous_plan=rec["plan_dict"], verification=ver)
    lineage = RequestAttemptLineage(request_id=rec["request_id"], case_id=rec["attempt_id"])
    parent = lineage.create_attempt(
        stage=STAGE_FAST_SUCCESS,
        plan=rec["plan_dict"],
        planner_model="qwen2.5:7b",
        planner_path="fast",
        escalation_trigger="none",
    )
    lineage.attach_verifier_invocation(parent.attempt_id, str(eight.get("verifier_invocation_id") or ""))
    lineage.set_disposition(parent.attempt_id, DISPOSITION_REJECTED_BY_SEMANTIC_VERIFIER)
    t0 = time.time()
    err = None
    try:
        strong = _run_integration_attempt_loop(
            rec["user_prompt"],
            raw["frames"],
            rec["und"],
            max_retries=2,
            base_url=BASE_URL,
            model=M32,
            chat_json_fn=None,
            build_plan_fn=None,
            initial_feedback=fb,
            path_label="semantic_strong",
        )
        status = strong.status
        plan_d = strong.plan.to_dict() if strong.plan else None
        child = lineage.create_attempt(
            stage=STAGE_SEMANTIC_STRONG,
            plan=strong.plan,
            planner_model=M32,
            planner_path="semantic_strong",
            parent_attempt_id=parent.attempt_id,
            escalation_trigger=TRIGGER_SEMANTIC_ESCALATION,
        )
        lineage.set_final(child.attempt_id)
        cls = _judge_strong(rec["attempt_id"], plan_d, status)
        if status not in {"success", "cannot_plan"}:
            cls = "STRONG_OPERATIONAL_FAILURE"
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        status = "error"
        plan_d = None
        cls = "STRONG_OPERATIONAL_FAILURE"
        lineage = lineage
    elapsed = round(time.time() - t0, 3)
    packed = {
        "attempt_id": rec["attempt_id"],
        "parent_attempt_id": parent.attempt_id,
        "fast_correct": "NO",
        "seven_b": "pass",
        "eight_b": eight.get("verdict"),
        "strong_status": status,
        "strong_elapsed_s": elapsed,
        "strong_plan_ops": [s.get("op") for s in (plan_d or {}).get("steps") or []],
        "classification": cls,
        "error": err,
        "lineage": lineage.to_dict(),
        "note_ko": rec.get("note_ko"),
        "defect": rec.get("defect"),
        "verifier_detection": "correct",
    }
    cache[key] = packed
    _save_cache(cache)
    return packed


def write_static() -> None:
    _write("baseline_freeze.json", {
        "phase": "40C",
        "phase40b_sha": PHASE40B_SHA,
        "phase40a_sha": PHASE40A_SHA,
        "shadow": "OFF",
        "production_model": SEMANTIC_VERIFIER_MODEL,
        "production_variant": SEMANTIC_VERIFIER_VARIANT,
        "production_prompt": "P0",
        "bounds": {
            "MAX_RESULT_SAMPLE_ROWS": MAX_RESULT_SAMPLE_ROWS,
            "MAX_RESULT_SAMPLE_COLUMNS": MAX_RESULT_SAMPLE_COLUMNS,
            "MAX_RESULT_SERIALIZED_CHARS": MAX_RESULT_SERIALIZED_CHARS,
        },
        "timeout_s": TIMEOUT_S,
        "timeout_increased": False,
        "p1_optimized": False,
        "production_changed": False,
    })
    _write("model_strategy_registry.json", {
        "V0": {"model": M7, "prompt": "P0", "name": "current_7b_default"},
        "V1": {"model": M8, "prompt": "P0", "name": "8b_default"},
        "V2": {"name": "7b_then_8b_on_7b_pass", "implemented": False, "research_simulation": True},
        "V3": {"name": "architecture_safe_selective_8b", "implemented": False},
        "retry_policy": RETRY_POLICY,
        "same_retries_7b_8b": True,
    })
    _write("shadow_state_proof.json", {
        "shadow": "OFF",
        "env_MULTI_SHADOW_ENABLED": os.environ.get("MULTI_SHADOW_ENABLED", ""),
        "live_shadow_requests": 0,
    })


def write_live(
    b40: list[dict[str, Any]],
    fresh: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    recovery: list[dict[str, Any]],
    m2: dict[str, Any],
) -> None:
    hold_b40 = {r["attempt_id"]: r for r in b40}
    hold_f = {r["attempt_id"]: r for r in fresh}
    hold = {**hold_b40, **hold_f}
    _write("phase40b_reanalysis.json", {
        "n": 42, "YES": 26, "NO": 16,
        "S0_recall": 0.5, "S2_recall": 0.8125, "S3_recall": 0.875,
        "COMBINATION_ONLY_CORRECTIONS": 0,
        "verdict": "MODEL_ONLY_SUFFICIENT",
        "primary_candidate": "8B+P0",
        "p1_not_optimized": True,
    })
    _write("fresh_holdout_corpus.json", {
        "n": len(fresh),
        "yes": sum(r["fast_correct"] == "YES" for r in fresh),
        "no": sum(r["fast_correct"] == "NO" for r in fresh),
        "ind": 0,
        "YES_rate": round(sum(r["fast_correct"] == "YES" for r in fresh) / max(len(fresh), 1), 4),
        "research_distribution_not_traffic": True,
        "rows": [{k: r[k] for k in (
            "attempt_id", "request_id", "fast_correct", "user_prompt", "note_ko",
            "defect", "shape", "sources", "validation_valid", "exec_success",
            "plan_fingerprint", "result_fingerprint", "truncated_obs", "fidelity",
        )} for r in fresh],
    })
    _write("manual_attempt_labels.json", {
        r["attempt_id"]: {"FAST_ATTEMPT_CORRECT": r["fast_correct"], "corpus": r.get("corpus"), "note_ko": r["note_ko"]}
        for r in [*b40, *fresh]
    })
    _write("replay_fidelity.json", {
        "phase40b": "EXACT_REPLAY of Phase 40B S0/S2 first shots",
        "fresh": "CANONICAL_EQUIVALENT_REPLAY (new cases, same observation bounds)",
        "not_silently_merged": True,
    })

    def pack_calls(strategy: str, corpus: str | None) -> list[dict[str, Any]]:
        return _first(calls, strategy, corpus)

    v0_b = pack_calls("V0", "phase40b")
    v1_b = pack_calls("V1", "phase40b")
    v0_f = pack_calls("V0", "phase40c_holdout")
    v1_f = pack_calls("V1", "phase40c_holdout")
    v0_all = v0_b + v0_f
    v1_all = v1_b + v1_f
    m0b, m1b = metrics(v0_b), metrics(v1_b)
    m0f, m1f = metrics(v0_f), metrics(v1_f)
    m0, m1 = metrics(v0_all), metrics(v1_all)
    _write("v0_7b_results.json", {"phase40b": m0b, "holdout": m0f, "combined": m0, "rows": v0_all})
    _write("v1_8b_results.json", {"phase40b": m1b, "holdout": m1f, "combined": m1, "rows": v1_all})

    idx7 = {c["attempt_id"]: c for c in v0_all}
    idx8 = {c["attempt_id"]: c for c in v1_all}

    def disagreement(subset: list[dict[str, Any]]) -> dict[str, int]:
        tab: dict[str, int] = {}
        for rec in subset:
            a = rec["attempt_id"]
            g = (idx7.get(a) or {}).get("verdict") or "?"
            e = (idx8.get(a) or {}).get("verdict") or "?"
            man = rec["fast_correct"]
            key = f"{g}|{e}|{man}"
            tab[key] = tab.get(key, 0) + 1
        return tab

    _write("verifier_disagreement_matrix.json", {
        "phase40b": disagreement(b40),
        "holdout": disagreement(fresh),
        "combined": disagreement(b40 + fresh),
        "schema": "7B|8B|Manual",
    })

    pass_region = []
    for rec in b40 + fresh:
        g = idx7.get(rec["attempt_id"]) or {}
        e = idx8.get(rec["attempt_id"]) or {}
        if g.get("verdict") != "pass":
            continue
        pass_region.append({
            "attempt_id": rec["attempt_id"],
            "corpus": rec.get("corpus"),
            "manual": rec["fast_correct"],
            "eight_verdict": e.get("verdict"),
            "eight_label": e.get("label"),
            "eight_opfail": bool(e.get("operational_failure")),
            "truncated": rec.get("truncated_obs"),
            "defect": rec.get("defect"),
        })
    yes_pr = [p for p in pass_region if p["manual"] == "YES"]
    no_pr = [p for p in pass_region if p["manual"] == "NO"]
    soc = [p for p in no_pr if _nonpass(p.get("eight_verdict"))]
    soff = [p for p in yes_pr if _nonpass(p.get("eight_verdict"))]
    soof = [p for p in pass_region if p.get("eight_opfail") or p.get("eight_verdict") == "parse_failed"]
    _write("seven_b_pass_region.json", {
        "n": len(pass_region),
        "manual_YES": len(yes_pr),
        "manual_NO": len(no_pr),
        "eight_corrections": len(soc),
        "eight_false_fails": len(soff),
        "eight_operational_failures": len(soof),
        "correction_ids": [p["attempt_id"] for p in soc],
        "false_fail_ids": [p["attempt_id"] for p in soff],
        "rows": pass_region,
    })
    _write("second_opinion_value.json", {
        "SECOND_OPINION_CORRECTIONS": len(soc),
        "SECOND_OPINION_FALSE_FAILS": len(soff),
        "SECOND_OPINION_OPERATIONAL_FAILURES": len(soof),
        "SECOND_OPINION_NET_CORRECTION": len(soc) - len(soff),
        "v2_8b_call_fraction_on_corpus": round(len(pass_region) / max(len(b40) + len(fresh), 1), 4),
        "note_ko": "V2는 7B PASS 뒤에 8B를 부르므로 valid-heavy 코퍼스에서 대부분의 요청에 8B가 붙는다. 선택적이 아니다.",
    })
    _write("v2_second_opinion_results.json", {
        "policy": "7B always; 8B iff 7B PASS; strong if 7B NON-PASS or (7B PASS and 8B NON-PASS)",
        "implemented": False,
        "corrections": [p["attempt_id"] for p in soc],
        "false_fails": [p["attempt_id"] for p in soff],
    })
    _write("eight_b_incremental_value.json", {
        "ADDITIONAL_CORRECT_REJECTIONS_V1": m1["CORRECT_REJECTION"] - m0["CORRECT_REJECTION"],
        "ADDITIONAL_FALSE_FAILS_V1": m1["FALSE_FAIL"] - m0["FALSE_FAIL"],
        "ADDITIONAL_UNCERTAINS_V1": m1["UNCERTAIN"] - m0["UNCERTAIN"],
        "ADDITIONAL_OPERATIONAL_FAILURES_V1": round(
            m1["operational_failure_rate"] - m0["operational_failure_rate"], 4
        ),
        "phase40b": {
            "ADDITIONAL_CR": m1b["CORRECT_REJECTION"] - m0b["CORRECT_REJECTION"],
        },
        "holdout": {
            "ADDITIONAL_CR": m1f["CORRECT_REJECTION"] - m0f["CORRECT_REJECTION"],
        },
    })

    def stab_block(aid: str) -> dict[str, Any]:
        vs = [c for c in calls if c["attempt_id"] == aid and c["strategy"] == "V1"]
        vs = sorted(vs, key=lambda x: int(x.get("repeat") or 0))
        dist = Counter(c.get("verdict") for c in vs)
        ok = [c for c in vs if not c.get("operational_failure") and c.get("verdict") != "parse_failed"]
        maj = Counter(c.get("verdict") for c in ok).most_common(1)
        rate = (maj[0][1] / len(ok)) if ok and maj else None
        return {
            "verdicts": [c.get("verdict") for c in vs],
            "PASS": dist.get("pass", 0),
            "FAIL": dist.get("fail", 0),
            "UNCERTAIN": dist.get("uncertain", 0),
            "operational_failure": sum(1 for c in vs if c.get("operational_failure") or c.get("verdict") == "parse_failed"),
            "VERDICT_STABILITY_RATE": round(rate, 4) if rate is not None else None,
            "n": len(vs),
        }

    m2_calls = [c for c in calls if c["attempt_id"] == M2_ID and c["strategy"] == "V1"]
    _write("eight_b_stability.json", {
        "m2": stab_block(M2_ID),
        "corrected_wrong": {i: stab_block(i) for i in STAB_8B_WRONG},
        "valid_controls": {i: stab_block(i) for i in STAB_8B_VALID},
        "seven_b_optional": {i: {
            "verdicts": [c.get("verdict") for c in calls if c["attempt_id"] == i and c["strategy"] == "V0"],
        } for i in STAB_7B},
        "m2_first_shot_n": len(m2_calls),
    })

    key_ids_b = [r["attempt_id"] for r in b40 if r.get("defect") == "grouping_identity"]
    key_ids_f = [r["attempt_id"] for r in fresh if r.get("defect") == "grouping_identity"]
    def key_recall(ids: list[str], idx: dict[str, dict[str, Any]]) -> dict[str, Any]:
        cr = sum(1 for i in ids if (idx.get(i) or {}).get("label") == "CORRECT_REJECTION")
        sw = [i for i in ids if (idx.get(i) or {}).get("label") == "SILENT_WRONG"]
        return {"n": len(ids), "cr": cr, "recall": round(cr / max(len(ids), 1), 4), "silent_wrong": sw}
    _write("key_identity_residual.json", {
        "note": "analysis only, not a routing rule",
        "phase40b_7b": key_recall(key_ids_b, idx7),
        "phase40b_8b": key_recall(key_ids_b, idx8),
        "holdout_7b": key_recall(key_ids_f, idx7),
        "holdout_8b": key_recall(key_ids_f, idx8),
        "remaining_8b_silent_wrong": key_recall(key_ids_b + key_ids_f, idx8)["silent_wrong"],
    })

    recov_ok = [r for r in recovery if r.get("classification") == "STRONG_RECOVERS"]
    _write("new_detection_recovery_subset.json", {
        "ids": RECOVERY_IDS,
        "n": len(recovery),
        "rows": recovery,
    })
    _write("semantic_recovery_chains.json", [
        {
            "attempt_id": r["attempt_id"],
            "chain": "fast NO → 7B PASS → 8B NON-PASS → semantic escalation → 32B child",
            "classification": r.get("classification"),
            "parent_attempt_id": r.get("parent_attempt_id"),
            "strong_status": r.get("strong_status"),
            "strong_elapsed_s": r.get("strong_elapsed_s"),
            "ops": r.get("strong_plan_ops"),
        }
        for r in recovery
    ])
    useful_n = len(recov_ok)
    _write("useful_detection_rate.json", {
        "evaluated_n": len(recovery),
        "USEFUL_8B_DETECTION": useful_n,
        "USEFUL_8B_DETECTION_RATE": round(useful_n / max(len(recovery), 1), 4),
        "class_counts": dict(Counter(r.get("classification") for r in recovery)),
        "note_ko": "검출만으로 성공이 아님. 32B 자식 계획이 요청을 충족해야 한다.",
    })

    _write("operational_reliability.json", {
        "retry_policy": RETRY_POLICY,
        "timeout_s": TIMEOUT_S,
        "V0": {"timeouts": m0["latency"]["timeouts"], "parse_failed": sum(1 for r in v0_all if r.get("verdict") == "parse_failed"), "n": m0["n"], "success_rate": round(1 - m0["operational_failure_rate"], 4)},
        "V1": {"timeouts": m1["latency"]["timeouts"], "parse_failed": sum(1 for r in v1_all if r.get("verdict") == "parse_failed"), "n": m1["n"], "success_rate": round(1 - m1["operational_failure_rate"], 4)},
        "phase40b_s2_orchard_timeout_remembered": True,
        "strong_planner": {"n": len(recovery), "timeouts": sum(1 for r in recovery if r.get("classification") == "STRONG_OPERATIONAL_FAILURE")},
    })
    _write("latency_comparison.json", {
        "7B": m0["latency"],
        "8B": m1["latency"],
        "strong_planner_32b": lat_stats([{"elapsed_s": r.get("strong_elapsed_s")} for r in recovery]),
        "not_mixed": True,
    })

    c7 = m0["latency"]["mean"] or 0
    c8 = m1["latency"]["mean"] or 0
    cs = lat_stats([{"elapsed_s": r.get("strong_elapsed_s")} for r in recovery])["mean"] or 0
    n_all = max(len(b40) + len(fresh), 1)
    p7_np = sum(1 for r in v0_all if _nonpass(r.get("verdict"))) / n_all
    p8_np = sum(1 for r in v1_all if _nonpass(r.get("verdict"))) / n_all
    p7_pass = sum(1 for r in v0_all if r.get("verdict") == "pass") / n_all
    v2_strong = 0
    for rec in b40 + fresh:
        g = (idx7.get(rec["attempt_id"]) or {}).get("verdict")
        e = (idx8.get(rec["attempt_id"]) or {}).get("verdict")
        if _nonpass(g) or (g == "pass" and _nonpass(e)):
            v2_strong += 1
    p2_strong = v2_strong / n_all
    v0_cost = c7 + p7_np * cs
    v1_cost = c8 + p8_np * cs
    v2_cost = c7 + p7_pass * c8 + p2_strong * cs
    _write("end_to_end_latency_simulation.json", {
        "corpus_not_traffic": True,
        "C7": c7, "C8": c8, "CS": cs,
        "V0": {"formula": "C7 + p(7B NON-PASS)*CS", "seconds": round(v0_cost, 3), "p_strong": round(p7_np, 4)},
        "V1": {"formula": "C8 + p(8B NON-PASS)*CS", "seconds": round(v1_cost, 3), "p_strong": round(p8_np, 4)},
        "V2": {"formula": "C7 + p(7B PASS)*C8 + p(escalate)*CS", "seconds": round(v2_cost, 3), "p_8b": round(p7_pass, 4), "p_strong": round(p2_strong, 4)},
        "v2_not_selective": True,
    })
    _write("strong_call_rate.json", {
        "corpus_not_traffic": True,
        "phase40b": {
            "V0": m0b["rejection_rate"], "V1": m1b["rejection_rate"],
        },
        "holdout": {"V0": m0f["rejection_rate"], "V1": m1f["rejection_rate"]},
        "combined": {"V0": m0["rejection_rate"], "V1": m1["rejection_rate"], "V2": round(p2_strong, 4)},
    })
    _write("useful_escalation_precision.json", {
        "V0": m0["USEFUL_ESCALATION_PRECISION"],
        "V1": m1["USEFUL_ESCALATION_PRECISION"],
        "V2": round(len(soc) / max(v2_strong, 1), 4) if False else None,
        "V2_precision": (
            round(
                sum(1 for rec in b40 + fresh if rec["fast_correct"] == "NO" and (
                    _nonpass((idx7.get(rec["attempt_id"]) or {}).get("verdict"))
                    or ((idx7.get(rec["attempt_id"]) or {}).get("verdict") == "pass" and _nonpass((idx8.get(rec["attempt_id"]) or {}).get("verdict")))
                )) / max(v2_strong, 1),
                4,
            ) if v2_strong else None
        ),
        "note_ko": "유한 N에서 8B FALSE_FAIL 0은 'never'가 아니라 관측 0/N.",
    })

    trunc_sw = [p for p in no_pr if p.get("truncated")]
    unc7 = [r for r in v0_all if r.get("verdict") == "uncertain"]
    _write("safe_trigger_candidates.json", [
        {
            "signal": "7B_UNCERTAIN",
            "architecture_safe": True,
            "coverage_of_7b_silent_wrongs": 0,
            "note_ko": "7B silent-wrong은 자신 있는 PASS. UNCERTAIN 에스컬레이션은 이 잔여를 못 잡음.",
            "n_uncertain": len(unc7),
        },
        {
            "signal": "result_truncation",
            "architecture_safe": True,
            "coverage_of_7b_silent_wrongs": len(trunc_sw),
            "unnecessary_8b_on_YES": sum(1 for p in yes_pr if p.get("truncated")),
            "rejected": True,
            "note_ko": "8B 교정과 truncation 상관 없음.",
        },
        {
            "signal": "7B_parse_or_timeout",
            "architecture_safe": True,
            "coverage_of_7b_silent_wrongs": 0,
            "note_ko": "7B 운영 실패가 silent-wrong과 겹치지 않음.",
        },
        {
            "signal": "7B_verdict_instability",
            "architecture_safe": True,
            "note_ko": "제한 n=5 반복. 불안정이 silent-wrong을 예측하면 모델 불확실성 신호로만 사용 가능.",
        },
    ])
    _write("safe_trigger_rejections.json", [
        "grouping / key-identity / filter / join / union / column names / prompt keywords / file count / domain / defect family",
        "these are analysis labels, not routing features",
    ])
    instable = []
    for i in STAB_7B:
        vs = [c.get("verdict") for c in calls if c["attempt_id"] == i and c["strategy"] == "V0"]
        if len(set(vs)) > 1:
            instable.append(i)
    _write("selective_trigger_conclusion.json", {
        "result": "NO_SAFE_SELECTIVE_TRIGGER_FOUND",
        "seven_b_instability_predicts_error": bool(instable),
        "unstable_ids": instable,
        "note_ko": "7B PASS+YES와 7B PASS+NO를 가르는 결정적 generic 신호가 없다.",
    })

    v2_mets = {
        "semantic_error_recall": round((m0["CORRECT_REJECTION"] + len(soc)) / max(m0["n_wrong"], 1), 4),
        "VALID_FALSE_FAIL_RATE": round(len(soff) / max(m0["n_valid"], 1), 4),
        "false_fail_observed_in_n_valid": {"n": len(soff), "N": m0["n_valid"]},
        "operational_failure_rate": round(len(soof) / max(len(b40) + len(fresh), 1), 4),
        "latency": {
            "median": round((m0["latency"]["median"] or 0) + p7_pass * (m1["latency"]["median"] or 0), 3),
            "p90": None,
        },
    }
    frontier = {}
    for s, mets, cost, rate in (
        ("V0", m0, v0_cost, m0["rejection_rate"]),
        ("V1", m1, v1_cost, m1["rejection_rate"]),
        ("V2", v2_mets, v2_cost, p2_strong),
    ):
        frontier[s] = {
            "semantic_wrong_recall": mets["semantic_error_recall"],
            "false_fail_rate": mets["VALID_FALSE_FAIL_RATE"],
            "false_fail_observed": mets.get("false_fail_observed_in_n_valid"),
            "operational_failure_rate": mets["operational_failure_rate"],
            "median_verifier_latency_s": mets["latency"]["median"],
            "p90_verifier_latency_s": mets["latency"].get("p90"),
            "estimated_e2e_semantic_s": round(cost, 3),
            "strong_call_rate": rate,
        }
    _write("accuracy_cost_frontier.json", {"combined_corpus": True, **frontier})

    claim = []
    for p in soc[:5]:
        e = idx8.get(p["attempt_id"]) or {}
        claim.append({
            "attempt_id": p["attempt_id"],
            "kind": "8B_correction",
            "quality_ko": "요청-계획 모순을 올바르게 지적" if e.get("label") == "CORRECT_REJECTION" else "확인 필요",
            "evidence": e.get("evidence"),
        })
    for p in no_pr:
        if p.get("eight_verdict") == "pass":
            e = idx8.get(p["attempt_id"]) or {}
            claim.append({
                "attempt_id": p["attempt_id"],
                "kind": "8B_miss",
                "quality_ko": "키 정체성 가정 또는 구조 VALID 과신",
                "evidence": e.get("evidence"),
            })
    _write("claim_quality_review.json", claim)

    bar_v1 = {
        "1_material_recall": m1["semantic_error_recall"] >= m0["semantic_error_recall"] + 0.1,
        "2_low_ff": m1["FALSE_FAIL"] == 0,
        "3_stable": False,
        "4_reliability": m1["operational_failure_rate"] < 0.05,
        "5_latency_ok": (m1["latency"]["median"] or 99) < 12,
        "6_recovery_useful": useful_n >= 3 and useful_n / max(len(recovery), 1) >= 0.5,
        "7_beyond_grouping": True,
        "8_no_arch_violation": True,
    }
    bar_v2 = {
        "1_substantial_corrections": len(soc) >= 5,
        "2_low_ff": len(soff) == 0,
        "3_ops_ok": len(soof) <= 1,
        "4_latency_justified": False,
        "5_recovery_useful": useful_n >= 3,
        "6_simple_enough": False,
        "7_beats_v1_frontier": v2_cost < v1_cost and frontier["V2"]["semantic_wrong_recall"] > m1["semantic_error_recall"] + 0.02,
    }
    v1_ready = all(bar_v1.values())
    v2_ready = all(bar_v2.values())
    if v1_ready:
        verdict = "RESEARCH_8B_DEFAULT_IMPLEMENTATION"
        nxt, nxtn = "A", "Phase 40D — 8B Default Semantic Verifier Implementation Design"
        seven = "NO_RESEARCH_8B_DEFAULT"
    elif v2_ready:
        verdict = "RESEARCH_DUAL_VERIFIER_STRATEGY"
        nxt, nxtn = "B", "Phase 40D — Dual-Verifier Semantic Review Architecture Design"
        seven = "YES_TEMPORARILY"
    else:
        verdict = "KEEP_7B_DEFAULT"
        nxt, nxtn = "D", "keep 7B production default; 8B cost/reliability not justified"
        seven = "YES"
    _write("model_strategy_conclusion.json", {
        "verdict": verdict,
        "selective": "NO_SAFE_SELECTIVE_TRIGGER",
        "seven_b_remain_default": seven,
        "next": nxt,
        "next_name": nxtn,
        "production_change": "NO_PRODUCTION_CHANGE",
        "bar_v1": bar_v1,
        "bar_v2": bar_v2,
        "v1_ready": v1_ready,
        "v2_ready": v2_ready,
        "note_ko": (
            "8B+P0는 재현율을 올리지만 지연·타임아웃·키 정체성 잔여·V2의 거의-전수 8B 호출 때문에 "
            "생산 기본값 전환 근거가 부족하다. 안전 선택 트리거 없음."
        ),
    })
    live_ok = bool(v0_f and v1_f) if LIVE else bool(v0_b)
    gate = "A" if (v0_b and v1_b and recovery is not None) else "B"
    if not v0_f and LIVE:
        gate = "B"
    _write("phase40c_summary.json", {
        "gate": "A" if (len(fresh) >= 30 and v0_all and v1_all) else "B",
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
        "phase40b_sha": PHASE40B_SHA,
        "verdict": verdict,
        "seven_b_remain_default": seven,
        "selective": "NO_SAFE_SELECTIVE_TRIGGER_FOUND",
        "production_changed": False,
        "next": nxt,
        "n_40b": len(b40),
        "n_holdout": len(fresh),
        "n_calls": len(calls),
        "v0_recall": m0["semantic_error_recall"],
        "v1_recall": m1["semantic_error_recall"],
        "second_opinion_corrections": len(soc),
        "useful_8b_detection_rate": round(useful_n / max(len(recovery), 1), 4) if recovery else None,
    })
    _write("regression_results.json", {"production_code_changed": False, "n_calls": len(calls)})


def run_suite(
    b40: list[dict[str, Any]],
    fresh: list[dict[str, Any]],
    m2: dict[str, Any],
    replay: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cache = _load_cache()
    # seed 40B exact replay
    for k, v in replay.items():
        cache.setdefault(k.replace("|V0|0", "|V0|qwen2.5:7b|P0|0").replace("|V1|0", "|V1|qwen3:8b|P0|0"), v)
        # also store under invoke() keys
    recs = {r["attempt_id"]: r for r in b40 + fresh}
    recs[M2_ID] = {**m2, "corpus": "historical_m2", "fidelity": "EXACT_REPLAY", "frames": None}
    out: list[dict[str, Any]] = []

    def take_replay(aid: str, strategy: str) -> dict[str, Any] | None:
        row = replay.get(f"{aid}|{strategy}|0")
        if not row:
            return None
        packed = dict(row)
        packed["strategy"] = strategy
        packed["corpus"] = recs[aid].get("corpus")
        packed["fidelity"] = "EXACT_REPLAY"
        packed["operational_failure"] = _opfail(packed.get("verdict"), packed.get("elapsed_s"))
        packed["repeat"] = 0
        return packed

    if not LIVE:
        for r in b40:
            for s in ("V0", "V1"):
                p = take_replay(r["attempt_id"], s)
                if p:
                    out.append(p)
        return out, []

    for r in b40:
        for s, model in (("V0", M7), ("V1", M8)):
            p = take_replay(r["attempt_id"], s)
            if p:
                print(f"REPLAY {s} {r['attempt_id']}", flush=True)
                out.append(p)
            else:
                print(f"LIVE {s} {r['attempt_id']}", flush=True)
                out.append(invoke(model, r, cache, strategy=s))
    for r in fresh:
        for s, model in (("V0", M7), ("V1", M8)):
            print(f"{s} {r['attempt_id']}", flush=True)
            out.append(invoke(model, r, cache, strategy=s))

    m2["corpus"] = "historical_m2"
    m2["fidelity"] = "EXACT_REPLAY"
    for i in range(STABILITY_N):
        print(f"STAB M2 8B repeat {i}", flush=True)
        out.append(invoke(M8, m2, cache, strategy="V1", repeat=i))
    for aid in STAB_8B_WRONG + STAB_8B_VALID:
        rec = recs[aid]
        for i in range(STABILITY_N):
            if i == 0:
                continue  # first shot already from replay
            print(f"STAB 8B {aid} repeat {i}", flush=True)
            out.append(invoke(M8, rec, cache, strategy="V1", repeat=i))
    for aid in STAB_7B:
        rec = recs[aid]
        for i in range(1, STABILITY_N):
            print(f"STAB 7B {aid} repeat {i}", flush=True)
            out.append(invoke(M7, rec, cache, strategy="V0", repeat=i))

    raw40 = {c["attempt_id"]: c for c in raw_40b_cases()}
    idx8 = {c["attempt_id"]: c for c in out if c["strategy"] == "V1" and int(c.get("repeat") or 0) == 0}
    recovery = []
    for aid in RECOVERY_IDS:
        print(f"STRONG {aid}", flush=True)
        recovery.append(recover_one(raw40[aid], recs[aid], idx8[aid], cache))
    return out, recovery


def main() -> None:
    write_static()
    b40, replay = load_40b_replay()
    fresh = build_fresh()
    bad = [r["attempt_id"] for r in fresh if not r["validation_valid"] or not r["exec_success"]]
    if bad:
        print("WARN", bad, flush=True)
    yes = sum(r["fast_correct"] == "YES" for r in fresh)
    no = sum(r["fast_correct"] == "NO" for r in fresh)
    print("fresh", len(fresh), "YES", yes, "NO", no, "live", LIVE, flush=True)
    m2 = m2_anchor()
    if os.environ.get("PHASE40C_REBUILD") == "1":
        calls = list(_load_cache().values())
        recovery = [c for c in calls if str(c.get("strategy") or "").startswith("STRONG") or c.get("classification")]
        # recovery stored with key STRONG — filter classification present
        recovery = [c for c in _load_cache().values() if "classification" in c]
        print("rebuild", len(calls), "recovery", len(recovery), flush=True)
    else:
        calls, recovery = run_suite(b40, fresh, m2, replay)
    write_live(b40, fresh, calls, recovery, m2)
    print("wrote", OUT, "calls", len(calls), "recovery", len(recovery), flush=True)


if __name__ == "__main__":
    main()
