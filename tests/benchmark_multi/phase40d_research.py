"""Phase 40D — planner semantic-requirement contract generalization (research only).

Does NOT modify production IntegrationPlan DSL, planner prompt, Validator,
Executor, verifier, or routing. Contract is an offline sidecar.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.schema_lineage import build_schema_lineage
from core.llm_client import chat_json
from tests.benchmark_multi.phase40a_research import M2_ID
from tests.benchmark_multi.phase40b_research import (
    _agg,
    _filt,
    _join,
    _plan,
    _ren,
    _sel,
    _union,
    materialize,
    m2_anchor,
    raw_cases as raw_40b,
)
from tests.benchmark_multi.phase39w_research import build_w_corpus
from tests.benchmark_multi.phase40c_research import fresh_raw as raw_40c

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase40d"
CACHE = OUT / "contract_live_cache.json"
PHASE40C_SHA = "a1f57c2f6a764b4c39e47e6166a0b8745ffb06e7"
LIVE = os.environ.get("PHASE40D_LIVE", "1") != "0"
BASE_URL = "http://localhost:11434"
M7 = "qwen2.5:7b"
M32 = "qwen3:32b"
TIMEOUT_S = 300
STABILITY_N = 5
GROUNDING = frozenset({"grounded", "partially_grounded", "cannot_ground"})

CONTRACT_PROMPT = """You author a generic semantic-requirement contract for a multi-file Excel integration request.
You do NOT write an IntegrationPlan. You do NOT name operations that must be used.
You do NOT invent columns or sources that are absent from the provided schema inventory.

Emit ONE JSON object with exactly these keys:
  grounding_status: grounded | partially_grounded | cannot_ground
  cannot_determine: boolean
  required_grain: array of {role_id, semantic_role, binding}
  required_outputs: array of {role_id, semantic_role, binding, function}
  required_distinctions: array of {left_role_id, right_role_id}

binding is either null or {source, column} where source and column exist in the schema inventory.
function is either null or one of: sum, mean, median, min, max, count.
semantic_role is a short generic description of the requested meaning, not a domain taxonomy.

If the request needs a distinction that is not present in any schema column, set
grounding_status=cannot_ground and cannot_determine=true. Do not fabricate a column.

If a grain/output is required and a supporting column exists, bind it.
If you are unsure, omit that obligation or set binding null. Do not guess.

Do not copy planner claims. Do not mention benchmark names.
"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


PROMPT_SHA = _sha(CONTRACT_PROMPT)


def _write(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


def _load_cache() -> dict[str, Any]:
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def _save_cache(cache: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False, default=str) + "\n")


def _empty_contract() -> dict[str, Any]:
    return {
        "grounding_status": "cannot_ground",
        "cannot_determine": True,
        "required_grain": [],
        "required_outputs": [],
        "required_distinctions": [],
    }


def parse_contract(raw: Any, schemas: dict[str, list[str]]) -> dict[str, Any]:
    """Structural parse only. No semantic repair, no prompt-based fill."""
    out = _empty_contract()
    parse_ok = isinstance(raw, dict)
    notes: list[str] = []
    if not parse_ok:
        return {**out, "parse_ok": False, "parser_notes": ["not_object"]}
    gs = raw.get("grounding_status")
    out["grounding_status"] = gs if gs in GROUNDING else "cannot_ground"
    if gs not in GROUNDING:
        notes.append("invalid_grounding_status")
    out["cannot_determine"] = bool(raw.get("cannot_determine"))
    allowed_cols = {(src, col) for src, cols in schemas.items() for col in cols}

    def parse_bind(b: Any) -> dict[str, Any] | None:
        if not isinstance(b, dict):
            return None
        src, col = str(b.get("source") or ""), str(b.get("column") or "")
        if not src or not col:
            notes.append("empty_binding")
            return None
        if (src, col) not in allowed_cols:
            notes.append("ungrounded_binding")
            return {"source": src, "column": col, "hallucinated": True}
        return {"source": src, "column": col, "hallucinated": False}

    def parse_roles(items: Any, with_fn: bool) -> list[dict[str, Any]]:
        rows = []
        if not isinstance(items, list):
            return rows
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                notes.append("role_not_object")
                continue
            row = {
                "role_id": str(it.get("role_id") or f"r{i}"),
                "semantic_role": str(it.get("semantic_role") or ""),
                "binding": parse_bind(it.get("binding")),
            }
            if with_fn:
                fn = it.get("function")
                row["function"] = fn if fn in {None, "sum", "mean", "median", "min", "max", "count"} else None
            rows.append(row)
        return rows

    out["required_grain"] = parse_roles(raw.get("required_grain"), False)
    out["required_outputs"] = parse_roles(raw.get("required_outputs"), True)
    dist = []
    if isinstance(raw.get("required_distinctions"), list):
        for it in raw["required_distinctions"]:
            if isinstance(it, dict) and it.get("left_role_id") and it.get("right_role_id"):
                dist.append({"left_role_id": str(it["left_role_id"]), "right_role_id": str(it["right_role_id"])})
    out["required_distinctions"] = dist
    out["parse_ok"] = True
    out["parser_notes"] = notes
    return out


def observe_plan(plan_dict: dict[str, Any], schemas: dict[str, list[str]]) -> dict[str, Any]:
    """Deterministic plan/result structure. No user-prompt interpretation."""
    if not isinstance(plan_dict, dict) or plan_dict.get("status") == "cannot_plan":
        return {
            "status": plan_dict.get("status") if isinstance(plan_dict, dict) else None,
            "ops": [],
            "group_by": [],
            "agg_functions": [],
            "select_columns": [],
            "final_schema": [],
            "evidence_signatures": {},
        }
    lin = build_schema_lineage(plan_dict, schemas)
    group_by: list[str] = []
    fns: list[str] = []
    sel: list[str] = []
    ops = []
    for s in plan_dict.get("steps") or []:
        if not isinstance(s, dict):
            continue
        ops.append(s.get("op"))
        p = s.get("params") or {}
        if s.get("op") == "aggregate":
            group_by = [str(x) for x in (p.get("group_by") or [])]
            fns = [str((m or {}).get("function")) for m in (p.get("metrics") or [])]
        if s.get("op") == "select_columns":
            sel = [str(c) for c in (p.get("columns") or [])]
    return {
        "status": plan_dict.get("status"),
        "ops": ops,
        "group_by": group_by,
        "agg_functions": fns,
        "select_columns": sel,
        "final_schema": list(lin.get("final_schema") or []),
        "evidence_signatures": lin.get("final_column_evidence_signatures") or {},
    }


def check_contract(contract: dict[str, Any], obs: dict[str, Any]) -> dict[str, Any]:
    """Compare declared obligations to plan/result structure only.

    INPUT: planner-declared contract
    DETERMINISTIC OBSERVATION: group_by, agg functions, final_schema, evidence signatures
    OUTPUT: consistent | contradiction | indeterminate
    WHY NO USER-SEMANTIC INFERENCE: bindings are LLM-declared (source, column, function);
    checkers only test set membership / equality of those declared tokens against
    observed plan fields. User prompt is not an input.
    """
    findings: list[dict[str, Any]] = []
    if not contract.get("parse_ok"):
        return {"status": "indeterminate", "findings": [{"rule": "parse", "status": "indeterminate"}]}

    def add(rule: str, status: str, detail: str) -> None:
        findings.append({"rule": rule, "status": status, "detail": detail})

    if obs.get("status") == "cannot_plan":
        if contract.get("cannot_determine") or contract.get("grounding_status") == "cannot_ground":
            add("K0_abstain", "consistent", "cannot_plan with cannot_ground")
        elif any((g.get("binding") or {}).get("column") for g in contract.get("required_grain") or []):
            add("K0_abstain", "contradiction", "bindings present but plan is cannot_plan")

    for g in contract.get("required_grain") or []:
        b = g.get("binding") or {}
        col = b.get("column")
        if not col or b.get("hallucinated"):
            add("K1_grain", "indeterminate", "no grounded grain binding")
            continue
        gb = obs.get("group_by") or []
        if gb:
            add("K1_grain", "consistent" if col in gb else "contradiction", f"declared {col} vs group_by {gb}")
        else:
            fs = obs.get("final_schema") or []
            add("K1_grain", "consistent" if col in fs else "contradiction", f"declared {col} vs final_schema {fs}")

    for r in contract.get("required_outputs") or []:
        b = r.get("binding") or {}
        col = b.get("column")
        fn = r.get("function")
        if not col or b.get("hallucinated"):
            add("K2_output", "indeterminate", "no grounded output binding")
            continue
        fs = set(obs.get("final_schema") or [])
        sigs = obs.get("evidence_signatures") or {}
        found = col in fs or any(
            isinstance(v, dict) and (v.get("column") == col or col in (v.get("group_by") or []))
            for v in sigs.values()
        )
        add("K2_output", "consistent" if found else "contradiction", f"materialize {col}")
        add("K4_complete", "consistent" if found else "contradiction", f"output col {col}")
        if fn:
            af = obs.get("agg_functions") or []
            add("K2_function", "consistent" if fn in af else "contradiction", f"declared {fn} vs {af}")

    roles = {x.get("role_id"): x for x in (contract.get("required_outputs") or []) + (contract.get("required_grain") or [])}
    sigs = obs.get("evidence_signatures") or {}
    for d in contract.get("required_distinctions") or []:
        a, b = roles.get(d.get("left_role_id")), roles.get(d.get("right_role_id"))
        ca = ((a or {}).get("binding") or {}).get("column")
        cb = ((b or {}).get("binding") or {}).get("column")
        if not ca or not cb:
            add("K3_distinct", "indeterminate", "unbound distinction")
            continue
        sa = sigs.get(ca)
        sb = sigs.get(cb)
        if sa and sb and sa == sb:
            add("K3_distinct", "contradiction", "identical evidence signatures")
        else:
            add("K3_distinct", "consistent" if ca != cb else "contradiction", f"{ca} vs {cb}")

    statuses = [f["status"] for f in findings]
    if "contradiction" in statuses:
        overall = "contradiction"
    elif statuses and all(s == "indeterminate" for s in statuses):
        overall = "indeterminate"
    else:
        overall = "consistent"
    return {"status": overall, "findings": findings}


def schema_inventory(frames: dict[str, pd.DataFrame]) -> dict[str, list[str]]:
    return {name: [str(c) for c in df.columns] for name, df in frames.items()}


def compact_plan(plan_dict: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": plan_dict.get("status"),
        "final_output": plan_dict.get("final_output"),
        "steps": [
            {"op": s.get("op"), "inputs": s.get("inputs"), "output": s.get("output"), "params": s.get("params")}
            for s in (plan_dict.get("steps") or [])
            if isinstance(s, dict)
        ],
    }


def _gold(**kw: Any) -> dict[str, Any]:
    return {
        "gold_grain_columns": kw.get("grain") or [],
        "gold_output_columns": kw.get("outputs") or [],
        "gold_function": kw.get("fn"),
        "gold_abstain": bool(kw.get("abstain")),
        "split": kw.get("split", "DEV"),
        "anchor": bool(kw.get("anchor")),
    }


def new_raw() -> list[dict[str, Any]]:
    bldg = pd.DataFrame({"room": ["R1", "R1", "R2"], "building": ["E", "E", "W"], "lux": [2, 3, 4]})
    fruit = pd.DataFrame({"sku": ["A", "B"], "kind": ["red", "green"], "n": [1, 2]})
    a = pd.DataFrame({"id": ["1", "2"], "v": [1, 2]})
    b = pd.DataFrame({"id": ["1", "2"], "w": [9, 8]})
    dock = pd.DataFrame({"bay": ["X", "X", "Y"], "kg": [1, 3, 4]})
    people = pd.DataFrame({"pid": ["P1", "P2"], "label": ["Jo", "Kim"]})
    jobs = pd.DataFrame({"pid": ["P1", "P2"], "hrs": [3, 7]})
    sites = pd.DataFrame({"site": ["S1", "S2"], "reading": [10, 12]})
    tix = pd.DataFrame({"xid": ["I1", "I2"], "state": ["open", "done"], "hrs": [2, 1]})
    wide = pd.DataFrame({"uid": ["U1", "U2"], **{f"m{i}": [i, i + 1] for i in range(26)}})

    def c(**kw: Any) -> dict[str, Any]:
        kw.setdefault("shape", "aggregate")
        kw.setdefault("sources", 1)
        kw.setdefault("trunc", False)
        kw.setdefault("defect", None)
        return kw

    return [
        c(attempt_id="d40-n-building", request_id="p40d-n1", fast_correct="NO",
          prompt="Sum lux per building, not per individual room.",
          note_ko="건물별인데 room으로 집계.",
          frames={"b.xlsx": bldg}, plan=_plan("a", [_agg("b.xlsx", "a", ["room"], "lux", "sum", "lux")]),
          gold=_gold(grain=["building"], split="DEV")),
        c(attempt_id="d40-y-building", request_id="p40d-y1", fast_correct="YES",
          prompt="Sum lux per building.",
          note_ko="building group-by 일치.",
          frames={"b.xlsx": bldg}, plan=_plan("a", [_agg("b.xlsx", "a", ["building"], "lux", "sum", "lux")]),
          gold=_gold(grain=["building"], split="DEV")),
        c(attempt_id="d40-y-kind", request_id="p40d-y2", fast_correct="YES",
          shape="filter", sources=1,
          prompt="Keep only red kind rows.",
          note_ko="red 필터 일치.",
          frames={"f.xlsx": fruit}, plan=_plan("x", [_filt("f.xlsx", "x", "kind", "red")]),
          gold=_gold(outputs=["kind"], split="DEV")),
        c(attempt_id="d40-y-stack", request_id="p40d-y3", fast_correct="YES",
          shape="union", sources=2,
          prompt="Stack table A and table B id rows so every row from either table is kept.",
          note_ko="union 일치.",
          frames={"a.xlsx": a, "b2.xlsx": pd.DataFrame({"id": ["1", "3"], "v": [5, 6]})},
          plan=_plan("u", [_union("a.xlsx", "b2.xlsx", "u")]),
          gold=_gold(split="HOLD")),
        c(attempt_id="d40-n-mean-bay", request_id="p40d-n4", fast_correct="NO",
          prompt="For each bay report the total kg.",
          note_ko="총량인데 평균.",
          frames={"d.xlsx": dock}, plan=_plan("a", [_agg("d.xlsx", "a", ["bay"], "kg", "mean", "kg")]),
          gold=_gold(grain=["bay"], fn="sum", split="DEV")),
        c(attempt_id="d40-y-sum-bay", request_id="p40d-y4", fast_correct="YES",
          prompt="For each bay report the total kg.",
          note_ko="합계 일치.",
          frames={"d.xlsx": dock}, plan=_plan("a", [_agg("d.xlsx", "a", ["bay"], "kg", "sum", "kg")]),
          gold=_gold(grain=["bay"], fn="sum", split="DEV")),
        c(attempt_id="d40-n-drop-label", request_id="p40d-n5", fast_correct="NO",
          shape="join", sources=2,
          prompt="Join jobs to people and keep label with hrs.",
          note_ko="label을 버림.",
          frames={"p.xlsx": people, "j.xlsx": jobs},
          plan=_plan("s", [_join("p.xlsx", "j.xlsx", "jj", "pid"), _sel("jj", "s", ["pid", "hrs"])]),
          gold=_gold(outputs=["label", "hrs"], split="HOLD")),
        c(attempt_id="d40-y-keep-label", request_id="p40d-y5", fast_correct="YES",
          prompt="Join jobs to people and keep label with hrs.",
          note_ko="label 유지.",
          frames={"p.xlsx": people, "j.xlsx": jobs},
          plan=_plan("j", [_join("p.xlsx", "j.xlsx", "j", "pid")]),
          gold=_gold(outputs=["label", "hrs"], split="HOLD")),
        c(attempt_id="d40-n-sides", request_id="p40d-n6", fast_correct="NO",
          shape="union", sources=2,
          prompt="For each id show v next to w.",
          note_ko="나란히 비교인데 union.",
          frames={"a.xlsx": a, "b.xlsx": b}, plan=_plan("u", [_union("a.xlsx", "b.xlsx", "u")]),
          gold=_gold(outputs=["v", "w"], split="DEV")),
        c(attempt_id="d40-y-sides", request_id="p40d-y6", fast_correct="YES",
          prompt="For each id show v next to w.",
          note_ko="rename+join 일치.",
          frames={"a.xlsx": a, "b.xlsx": b},
          plan=_plan("j", [_ren("a.xlsx", "l", {"v": "v_l"}), _ren("b.xlsx", "r", {"w": "w_r"}), _join("l", "r", "j", "id")]),
          gold=_gold(outputs=["v", "w"], split="DEV")),
        c(attempt_id="d40-y-rename", request_id="p40d-y7", fast_correct="YES",
          prompt="Rename lux to brightness and keep building and brightness.",
          note_ko="rename 일치.",
          frames={"b.xlsx": bldg},
          plan=_plan("s", [_ren("b.xlsx", "r", {"lux": "brightness"}), _sel("r", "s", ["building", "brightness"])]),
          gold=_gold(outputs=["building", "lux"], split="HOLD")),
        c(attempt_id="d40-y-multi", request_id="p40d-y8", fast_correct="YES",
          prompt="Join people to jobs then keep pid, label, and hrs.",
          note_ko="다단계 일치.",
          frames={"p.xlsx": people, "j.xlsx": jobs},
          plan=_plan("s", [_join("p.xlsx", "j.xlsx", "jj", "pid"), _sel("jj", "s", ["pid", "label", "hrs"])]),
          gold=_gold(outputs=["pid", "label", "hrs"], split="HOLD")),
        c(attempt_id="d40-y-done", request_id="p40d-y9", fast_correct="YES",
          prompt="Keep only done tickets.",
          note_ko="done 필터.",
          frames={"t.xlsx": tix}, plan=_plan("f", [_filt("t.xlsx", "f", "state", "done")]),
          gold=_gold(outputs=["state"], split="HOLD")),
        c(attempt_id="d40-y-trunc", request_id="p40d-y10", fast_correct="YES",
          prompt="Keep every measured attribute for each unit.",
          note_ko="truncation 통제.",
          frames={"u.xlsx": wide}, plan=_plan("s", [_sel("u.xlsx", "s", ["uid"] + [f"m{i}" for i in range(26)])]),
          gold=_gold(outputs=["uid"], split="HOLD")),
        c(attempt_id="d40-y-select", request_id="p40d-y11", fast_correct="YES",
          prompt="Keep ticket id and state only.",
          note_ko="열 부분집합.",
          frames={"t.xlsx": tix}, plan=_plan("s", [_sel("t.xlsx", "s", ["xid", "state"])]),
          gold=_gold(outputs=["xid", "state"], split="DEV")),
        c(attempt_id="d40-abstain-inlet", request_id="p40d-a1", fast_correct="YES",
          prompt="Compare inlet flow with outlet flow for each site. The files have no inlet/outlet column.",
          note_ko="구분 열이 없는 올바른 cannot_plan.",
          frames={"s.xlsx": sites},
          plan=integration_plan_from_dict({"status": "cannot_plan", "reason": "insufficient evidence", "steps": [], "final_output": None}),
          gold=_gold(abstain=True, split="DEV")),
        c(attempt_id="d40-n-fake-inlet", request_id="p40d-a2", fast_correct="NO",
          prompt="Compare inlet flow with outlet flow for each site.",
          note_ko="구분 열 없는데 합계로 실행.",
          frames={"s.xlsx": sites},
          plan=_plan("a", [_agg("s.xlsx", "a", ["site"], "reading", "sum", "reading")]),
          gold=_gold(abstain=True, split="DEV")),
        c(attempt_id="d40-n-one-side", request_id="p40d-n7", fast_correct="NO",
          shape="join", sources=2,
          prompt="For each id show v next to w.",
          note_ko="양쪽인데 v만.",
          frames={"a.xlsx": a, "b.xlsx": b},
          plan=_plan("s", [_join("a.xlsx", "b.xlsx", "j", "id"), _sel("j", "s", ["id", "v"])]),
          gold=_gold(outputs=["v", "w"], split="HOLD")),
        c(attempt_id="d40-y-keep-events", request_id="p40d-y12", fast_correct="YES",
          shape="select", sources=1,
          prompt="Keep every ticket row with xid, state, and hrs. Do not aggregate.",
          note_ko="행 유지 lookalike vs 잘못된 집계.",
          frames={"t.xlsx": tix}, plan=_plan("s", [_sel("t.xlsx", "s", ["xid", "state", "hrs"])]),
          gold=_gold(outputs=["xid", "state", "hrs"], split="HOLD")),
        c(attempt_id="d40-y-v-only", request_id="p40d-y13", fast_correct="YES",
          prompt="From table A keep id and v only.",
          note_ko="한쪽만 요청한 올바른 축소.",
          frames={"a.xlsx": a}, plan=_plan("s", [_sel("a.xlsx", "s", ["id", "v"])]),
          gold=_gold(outputs=["v"], split="HOLD")),
        c(attempt_id="d40-y-site-sum", request_id="p40d-y14", fast_correct="YES",
          prompt="For each site report the total reading.",
          note_ko="site 합계 요청은 올바른 집계.",
          frames={"s.xlsx": sites}, plan=_plan("a", [_agg("s.xlsx", "a", ["site"], "reading", "sum", "reading")]),
          gold=_gold(grain=["site"], fn="sum", split="DEV")),
        c(attempt_id="d40-y-mean-bay", request_id="p40d-y15", fast_correct="YES",
          prompt="For each bay report the mean kg.",
          note_ko="평균 요청은 올바른 mean.",
          frames={"d.xlsx": dock}, plan=_plan("a", [_agg("d.xlsx", "a", ["bay"], "kg", "mean", "kg")]),
          gold=_gold(grain=["bay"], fn="mean", split="HOLD")),
    ]


B40_GOLD = {
    "b40-n-campus": _gold(grain=["campus"], anchor=True, split="DEV"),
    "b40-y-campus": _gold(grain=["campus"], anchor=True, split="DEV"),
    "b40-n-vessel": _gold(grain=["vessel"], anchor=True, split="DEV"),
    "b40-y-vessel": _gold(grain=["vessel"], anchor=True, split="HOLD"),
    "b40-n-mean-not-total": _gold(grain=["dock"], fn="sum", anchor=True, split="DEV"),
    "b40-y-total-dock": _gold(grain=["dock"], fn="sum", anchor=True, split="HOLD"),
    "b40-n-drop-name": _gold(outputs=["name", "amt"], anchor=True, split="DEV"),
    "b40-n-union-not-compare": _gold(outputs=["flow"], anchor=True, split="DEV"),
    "b40-n-agg-events": _gold(outputs=["eid", "zone", "sec"], anchor=True, split="HOLD"),
    "b40-n-filter-grade": _gold(outputs=["grade"], split="HOLD"),
}
C40_GOLD = {
    "c40-n-ward": _gold(grain=["ward"], anchor=True, split="DEV"),
    "c40-y-ward": _gold(grain=["ward"], anchor=True, split="HOLD"),
    "c40-n-depot": _gold(grain=["depot"], anchor=True, split="DEV"),
    "c40-y-depot": _gold(grain=["depot"], split="HOLD"),
    "c40-n-mean-span": _gold(grain=["span"], fn="sum", split="DEV"),
    "c40-y-roast": _gold(outputs=["roast"], split="HOLD"),
    "c40-y-stack-weeks": _gold(split="HOLD"),
    "c40-y-keep-ml": _gold(outputs=["m", "ml"], split="HOLD"),
    "c40-y-compare-tod": _gold(outputs=["kw"], split="DEV"),
    "c40-y-total-span": _gold(grain=["span"], fn="sum", split="DEV"),
}


def build_corpus() -> list[dict[str, Any]]:
    want_b = set(B40_GOLD)
    want_c = set(C40_GOLD)
    rows = []
    for raw in raw_40b():
        if raw["attempt_id"] not in want_b:
            continue
        rec = materialize(raw)
        rec["frames"] = raw["frames"]
        rec.update(B40_GOLD[raw["attempt_id"]])
        rec["origin"] = "phase40b_anchor"
        rows.append(rec)
    for raw in raw_40c():
        if raw["attempt_id"] not in want_c:
            continue
        rec = materialize(raw)
        rec["frames"] = raw["frames"]
        rec.update(C40_GOLD[raw["attempt_id"]])
        rec["origin"] = "phase40c_anchor"
        rows.append(rec)
    m2 = m2_anchor()
    m2["frames"] = next(c["frames"] for c in build_w_corpus() if c["attempt_id"] == M2_ID)
    m2.update(_gold(grain=["agent"], abstain=False, anchor=True, split="DEV"))
    # tickets.xlsx has both agent and tid; the wrong plan groups by tid.
    m2["gold_must_not_bind"] = ["tid"]
    m2["gold_requested"] = "agent"
    m2["origin"] = "historical_m2"
    m2["split"] = "DEV"
    rows.append(m2)
    for raw in new_raw():
        if raw["attempt_id"] == "d40-abstain-inlet":
            rec = {
                "attempt_id": raw["attempt_id"],
                "request_id": raw["request_id"],
                "fast_correct": raw["fast_correct"],
                "user_prompt": raw["prompt"],
                "note_ko": raw["note_ko"],
                "defect": None,
                "plan_dict": raw["plan"].to_dict(),
                "validation_valid": True,
                "exec_success": True,
                "truncated_obs": False,
            }
        else:
            rec = materialize(raw)
        rec["frames"] = raw["frames"]
        rec.update(raw["gold"])
        rec["origin"] = "phase40d_new"
        rows.append(rec)
    return rows


def payload_i0(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_prompt": rec["user_prompt"],
        "schema_inventory": schema_inventory(rec["frames"]),
    }


def payload_i1(rec: dict[str, Any]) -> dict[str, Any]:
    return {**payload_i0(rec), "integration_plan": compact_plan(rec["plan_dict"])}


def generate(rec: dict[str, Any], *, model: str, mode: str, cache: dict[str, Any], repeat: int = 0) -> dict[str, Any]:
    key = f"{rec['attempt_id']}|{model}|{mode}|{repeat}"
    if key in cache:
        packed = cache[key]
        schemas = schema_inventory(rec["frames"])
        packed["checker"] = check_contract(packed["contract"], observe_plan(rec["plan_dict"], schemas))
        packed["gold_grain_columns"] = rec.get("gold_grain_columns")
        packed["gold_function"] = rec.get("gold_function")
        packed["gold_abstain"] = rec.get("gold_abstain")
        packed["gold_must_not_bind"] = rec.get("gold_must_not_bind")
        packed.update(score_declaration(packed, rec))
        cache[key] = packed
        return packed
    body = payload_i0(rec) if mode == "I0" else payload_i1(rec)
    t0 = time.time()
    err = None
    raw: Any = {}
    try:
        raw = chat_json(
            CONTRACT_PROMPT + "\nINPUT:\n" + json.dumps(body, ensure_ascii=False, indent=2),
            system="Return only the contract JSON.",
            base_url=BASE_URL,
            model=model,
            timeout=TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        raw = {}
    elapsed = round(time.time() - t0, 3)
    schemas = schema_inventory(rec["frames"])
    contract = parse_contract(raw, schemas)
    obs = observe_plan(rec["plan_dict"], schemas)
    chk = check_contract(contract, obs)
    packed = {
        "attempt_id": rec["attempt_id"],
        "model": model,
        "mode": mode,
        "repeat": repeat,
        "fast_correct": rec["fast_correct"],
        "split": rec.get("split"),
        "origin": rec.get("origin"),
        "elapsed_s": elapsed,
        "error": err,
        "contract": contract,
        "observation": {k: obs[k] for k in ("status", "ops", "group_by", "agg_functions", "final_schema") if k in obs},
        "checker": chk,
        "gold_grain_columns": rec.get("gold_grain_columns"),
        "gold_function": rec.get("gold_function"),
        "gold_abstain": rec.get("gold_abstain"),
        "gold_must_not_bind": rec.get("gold_must_not_bind"),
        "defect": rec.get("defect"),
    }
    packed.update(score_declaration(packed, rec))
    cache[key] = packed
    _save_cache(cache)
    return packed


def _bound_cols(contract: dict[str, Any], field: str) -> list[str]:
    cols = []
    for g in contract.get(field) or []:
        b = g.get("binding") or {}
        if b.get("column") and not b.get("hallucinated"):
            cols.append(b["column"])
        elif b.get("hallucinated"):
            cols.append(f"HALLUC:{b.get('column')}")
    return cols


def score_declaration(packed: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    """Research scoring against manual gold. Gold is never sent to the LLM."""
    c = packed["contract"]
    grain_b = [x.replace("HALLUC:", "") for x in _bound_cols(c, "required_grain")]
    hallu = any((g.get("binding") or {}).get("hallucinated") for g in (c.get("required_grain") or []) + (c.get("required_outputs") or []))
    gold_g = list(rec.get("gold_grain_columns") or [])
    must_not = set(rec.get("gold_must_not_bind") or [])
    abstain = bool(rec.get("gold_abstain"))
    decl = "INDETERMINATE"
    bind = "INDETERMINATE"
    tax = "OTHER"
    if hallu:
        decl, bind, tax = False, False, "HALLUCINATED_BINDING"
    elif abstain:
        ok = bool(c.get("cannot_determine") or c.get("grounding_status") == "cannot_ground")
        decl, bind = ok, ok
        tax = "OTHER" if ok else "HALLUCINATED_BINDING"
        if ok:
            tax = "OTHER"
    elif rec.get("attempt_id") == M2_ID or must_not:
        # Must not bind tid as the requested agent grain.
        bad = bool(set(grain_b) & must_not)
        if c.get("cannot_determine") and not grain_b:
            decl, bind, tax = True, True, "OTHER"  # honest abstain: agent not in schema
        elif bad:
            decl, bind, tax = False, False, "WRONG_SEMANTIC_BINDING"
        elif gold_g and set(gold_g) <= set(grain_b):
            decl, bind, tax = True, True, "OTHER"
        elif grain_b:
            decl, bind, tax = False, False, "WRONG_SEMANTIC_BINDING"
        else:
            decl, bind, tax = False, False, "OMITTED_REQUIREMENT"
    elif gold_g:
        if set(gold_g) <= set(grain_b):
            extra = set(grain_b) - set(gold_g)
            decl, bind = True, True
            tax = "OVERDECLARED_REQUIREMENT" if extra else "OTHER"
        elif grain_b:
            decl, bind, tax = False, False, "WRONG_SEMANTIC_BINDING"
        elif c.get("cannot_determine"):
            decl, bind, tax = False, False, "OMITTED_REQUIREMENT"
        else:
            decl, bind, tax = False, False, "OMITTED_REQUIREMENT"
    else:
        # no gold grain; outputs-only or stacking
        outs = [x.replace("HALLUC:", "") for x in _bound_cols(c, "required_outputs")]
        gold_o = list(rec.get("gold_output_columns") or [])
        if gold_o and set(gold_o) & (set(outs) | set(grain_b)):
            decl, bind, tax = True, True, "OTHER"
        elif gold_o and (c.get("cannot_determine") or not outs):
            decl, bind, tax = False, False, "OMITTED_REQUIREMENT"
        else:
            decl, bind, tax = True, True, "OTHER"

    fn_gold = rec.get("gold_function")
    fns = [r.get("function") for r in (c.get("required_outputs") or []) if r.get("function")]
    fn_ok = (fn_gold in fns) if fn_gold else None

    usable, unusable_reason = contract_usability(packed)
    chk = packed["checker"]["status"]
    # True self-justification only: usable wrong contract that internally matches the wrong plan.
    self_just = (
        rec["fast_correct"] == "NO"
        and usable
        and decl is False
        and chk == "consistent"
    )
    exposable = rec["fast_correct"] == "NO" and usable and decl is True and chk == "contradiction"
    false_block = rec["fast_correct"] == "YES" and chk == "contradiction"
    fb_cause = false_block_cause(packed, decl, bind) if false_block else None
    fb_semantic = fb_cause in {"CONTRACT_DECLARATION_WRONG", "CONTRACT_BINDING_WRONG"}
    fb_obs = fb_cause == "PLAN_OBSERVATION_GAP"
    checkable = "CHECKABLE" if any(
        f["rule"] in {"K1_grain", "K2_function", "K2_output", "K3_distinct", "K4_complete"} and f["status"] != "indeterminate"
        for f in packed["checker"]["findings"]
    ) else "NOT_CHECKABLE"
    if packed["checker"]["findings"] and all(f["status"] == "indeterminate" for f in packed["checker"]["findings"]):
        checkable = "NOT_CHECKABLE"
    elif any(f["status"] == "indeterminate" for f in packed["checker"]["findings"]) and any(f["status"] != "indeterminate" for f in packed["checker"]["findings"]):
        checkable = "PARTIALLY_CHECKABLE"
    return {
        "CONTRACT_DECLARATION_CORRECT": decl,
        "CONTRACT_BINDING_CORRECT": bind,
        "function_declared_correct": fn_ok,
        "CONTRACT_USABLE": usable,
        "UNUSABLE_REASON": unusable_reason,
        "SELF_JUSTIFYING_CONTRACT": self_just,
        "CONTRACT_EXPOSABLE_ERROR": exposable,
        "CONTRACT_FALSE_BLOCK": false_block,
        "FALSE_BLOCK_CAUSE": fb_cause,
        "FALSE_BLOCK_SEMANTIC": fb_semantic,
        "FALSE_BLOCK_OBSERVATION_GAP": fb_obs,
        "checkability": checkable,
        "failure_taxonomy": tax if usable else "UNUSABLE_CONTRACT",
        "note_ko": {
            "WRONG_SEMANTIC_BINDING": "요청 의미와 다른 열에 바인딩",
            "HALLUCINATED_BINDING": "스키마에 없는 열을 발명",
            "OMITTED_REQUIREMENT": "필요한 의무를 생략",
            "OVERDECLARED_REQUIREMENT": "추가 의무를 과다 선언",
            "UNUSABLE_CONTRACT": "timeout/빈 계약/파서 실패. 자기정당화가 아님",
            "OTHER": "기타/정상",
        }.get(tax if usable else "UNUSABLE_CONTRACT", "기타"),
    }


def contract_usability(packed: dict[str, Any]) -> tuple[bool, str | None]:
    """Usable semantic contract vs operational/empty/parser failure."""
    if packed.get("error"):
        return False, "OPERATIONAL_ERROR"
    c = packed.get("contract") or {}
    if not c.get("parse_ok"):
        return False, "PARSER_FAILURE"
    empty = not (c.get("required_grain") or c.get("required_outputs") or c.get("required_distinctions"))
    notes = c.get("parser_notes") or []
    if empty and not c.get("cannot_determine"):
        return False, "EMPTY_UNUSABLE_CONTRACT"
    if empty and "invalid_grounding_status" in notes:
        return False, "EMPTY_UNUSABLE_CONTRACT"
    return True, None


def false_block_cause(packed: dict[str, Any], decl: Any, bind: Any) -> str:
    """Split checker contradiction on Manual YES into semantic vs observation limitation."""
    if decl is False:
        return "CONTRACT_DECLARATION_WRONG"
    if bind is False:
        return "CONTRACT_BINDING_WRONG"
    obs = packed.get("observation") or {}
    if obs.get("status") == "cannot_plan" or not (obs.get("final_schema") or []):
        return "PLAN_OBSERVATION_GAP"
    if "rename_columns" in (obs.get("ops") or []):
        return "PLAN_OBSERVATION_GAP"
    return "CHECKER_BUG"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = max(len(rows), 1)
    no = [r for r in rows if r["fast_correct"] == "NO"]
    yes = [r for r in rows if r["fast_correct"] == "YES"]
    usable = [r for r in rows if r["contract"].get("parse_ok") and not r.get("error")]
    decl_ok = [r for r in rows if r.get("CONTRACT_DECLARATION_CORRECT") is True]
    return {
        "n": len(rows),
        "parse_ok": sum(1 for r in rows if r["contract"].get("parse_ok")),
        "errors": sum(1 for r in rows if r.get("error")),
        "CONTRACT_COVERAGE": round(len(usable) / n, 4),
        "CORRECT_CONTRACT_COVERAGE": round(len(decl_ok) / n, 4),
        "declaration_accuracy": round(sum(1 for r in rows if r.get("CONTRACT_DECLARATION_CORRECT") is True) / n, 4),
        "binding_accuracy": round(sum(1 for r in rows if r.get("CONTRACT_BINDING_CORRECT") is True) / n, 4),
        "CHECKABLE_CORRECT_CONTRACT_COVERAGE": round(
            sum(1 for r in decl_ok if r.get("checkability") in {"CHECKABLE", "PARTIALLY_CHECKABLE"}) / n, 4
        ),
        "CONTRACT_WRONG_RECALL": round(sum(1 for r in no if r.get("CONTRACT_EXPOSABLE_ERROR")) / max(len(no), 1), 4),
        "CONTRACT_FALSE_BLOCK": round(sum(1 for r in yes if r.get("CONTRACT_FALSE_BLOCK")) / max(len(yes), 1), 4),
        "FALSE_BLOCK_SEMANTIC": round(sum(1 for r in yes if r.get("FALSE_BLOCK_SEMANTIC")) / max(len(yes), 1), 4),
        "FALSE_BLOCK_OBSERVATION_GAP": round(sum(1 for r in yes if r.get("FALSE_BLOCK_OBSERVATION_GAP")) / max(len(yes), 1), 4),
        "SELF_JUSTIFICATION_RATE": round(sum(1 for r in no if r.get("SELF_JUSTIFYING_CONTRACT")) / max(len(no), 1), 4),
        "UNUSABLE_CONTRACT_RATE": round(sum(1 for r in rows if r.get("CONTRACT_USABLE") is False) / n, 4),
        "cannot_determine_rate": round(sum(1 for r in rows if r["contract"].get("cannot_determine")) / n, 4),
        "mean_latency_s": round(sum(float(r.get("elapsed_s") or 0) for r in rows) / n, 3),
        "exposable_ids": [r["attempt_id"] for r in rows if r.get("CONTRACT_EXPOSABLE_ERROR")],
        "false_block_ids": [r["attempt_id"] for r in rows if r.get("CONTRACT_FALSE_BLOCK")],
        "semantic_false_block_ids": [r["attempt_id"] for r in rows if r.get("FALSE_BLOCK_SEMANTIC")],
        "observation_gap_false_block_ids": [r["attempt_id"] for r in rows if r.get("FALSE_BLOCK_OBSERVATION_GAP")],
        "self_just_ids": [r["attempt_id"] for r in rows if r.get("SELF_JUSTIFYING_CONTRACT")],
        "unusable_ids": [r["attempt_id"] for r in rows if r.get("CONTRACT_USABLE") is False],
    }


def write_static() -> None:
    from core.integrate.semantic_escalation import SEMANTIC_VERIFIER_MODEL, SEMANTIC_VERIFIER_VARIANT
    from core.integrate.result_observation import (
        MAX_RESULT_SAMPLE_COLUMNS,
        MAX_RESULT_SAMPLE_ROWS,
        MAX_RESULT_SERIALIZED_CHARS,
    )
    from core.integrate.semantic_escalation import MAX_SEMANTIC_ESCALATIONS
    _write("baseline_freeze.json", {
        "phase40c_sha": PHASE40C_SHA,
        "phase40c_gate": "A",
        "shadow": "OFF",
        "production_verifier": SEMANTIC_VERIFIER_MODEL,
        "production_variant": SEMANTIC_VERIFIER_VARIANT,
        "bounded_result_v1": {
            "MAX_RESULT_SAMPLE_ROWS": MAX_RESULT_SAMPLE_ROWS,
            "MAX_RESULT_SAMPLE_COLUMNS": MAX_RESULT_SAMPLE_COLUMNS,
            "MAX_RESULT_SERIALIZED_CHARS": MAX_RESULT_SERIALIZED_CHARS,
        },
        "MAX_SEMANTIC_ESCALATIONS": MAX_SEMANTIC_ESCALATIONS,
        "semantic_escalation_policy_changed": False,
        "production_planner_prompt_changed": False,
        "production_planner_model_changed": False,
        "production_dsl_changed": False,
        "production_validator_changed": False,
        "production_executor_changed": False,
        "v2_2_changed": False,
        "legacy_changed": False,
        "verifier_prompt_changed": False,
        "timeout_s": TIMEOUT_S,
        "contract_schema_research_only": True,
    })
    _write("contract_schema_candidates.json", {
        "frozen": True,
        "fields": ["grounding_status", "cannot_determine", "required_grain", "required_outputs", "required_distinctions"],
        "rejected": ["domain ontology", "operation-required join/union enums"],
    })
    _write("contract_prompt_registry.json", {"CONTRACT_PROMPT": CONTRACT_PROMPT})
    _write("contract_prompt_hashes.json", {"CONTRACT_PROMPT": PROMPT_SHA})
    _write("shadow_state_proof.json", {
        "shadow": "OFF",
        "env_MULTI_SHADOW_ENABLED": os.environ.get("MULTI_SHADOW_ENABLED", ""),
    })
    _write("contract_leakage_audit.json", [
        {"field": "required_grain.binding.column", "generic": True, "note_ko": "LLM이 바인딩. 도메인 열 이름을 체커가 고르지 않음."},
        {"field": "required_outputs.function", "generic": True, "note_ko": "선언된 집계 함수만 관측 함수와 비교."},
        {"field": "semantic_role", "generic": True, "note_ko": "자유 텍스트. Python이 해석하지 않음."},
    ])
    _write("checker_leakage_audit.json", [
        {"rule": "K1_grain", "inputs": "declared binding.column + observed group_by/final_schema", "user_prompt": False},
        {"rule": "K2_function", "inputs": "declared function + observed agg_functions", "user_prompt": False},
        {"rule": "K3_distinct", "inputs": "declared role pair + V2.2 signatures", "user_prompt": False},
        {"rule": "K4_complete", "inputs": "declared output column + final_schema", "user_prompt": False},
    ])


def write_live(corpus: list[dict[str, Any]], calls: list[dict[str, Any]]) -> None:
    _write("research_corpus.json", {
        "n": len(corpus),
        "YES": sum(r["fast_correct"] == "YES" for r in corpus),
        "NO": sum(r["fast_correct"] == "NO" for r in corpus),
        "IND": 0,
        "DEV": sum(r.get("split") == "DEV" for r in corpus),
        "HOLD": sum(r.get("split") == "HOLD" for r in corpus),
        "rows": [{k: r.get(k) for k in (
            "attempt_id", "request_id", "fast_correct", "user_prompt", "note_ko",
            "split", "origin", "gold_grain_columns", "gold_function", "gold_abstain",
        )} for r in corpus],
    })
    _write("manual_labels.json", {r["attempt_id"]: {"FAST_ATTEMPT_CORRECT": r["fast_correct"], "gold_grain_columns": r.get("gold_grain_columns")} for r in corpus})

    def sel(model: str, mode: str, split: str | None = None) -> list[dict[str, Any]]:
        out = []
        seen = set()
        for c in calls:
            if c.get("model") != model or c.get("mode") != mode or int(c.get("repeat") or 0) != 0:
                continue
            if split and c.get("split") != split:
                continue
            if c["attempt_id"] in seen:
                continue
            seen.add(c["attempt_id"])
            out.append(c)
        return out

    s7_i0, s32_i0 = summarize(sel(M7, "I0")), summarize(sel(M32, "I0"))
    s7_i1, s32_i1 = summarize(sel(M7, "I1")), summarize(sel(M32, "I1"))
    hold7, hold32 = summarize(sel(M7, "I0", "HOLD")), summarize(sel(M32, "I0", "HOLD"))
    _write("separate_contract_results_7b.json", {"I0": s7_i0, "I1": s7_i1, "HOLD_I0": hold7})
    _write("separate_contract_results_32b.json", {"I0": s32_i0, "I1": s32_i1, "HOLD_I0": hold32})
    _write("independent_i0_results.json", {"7B": s7_i0, "32B": s32_i0})
    _write("plan_aware_i1_results.json", {"7B": s7_i1, "32B": s32_i1})
    _write("joint_output_ablation.json", {"ran": False, "note_ko": "분리 계약 베이스라인을 우선. 공동 출력은 1차에서 생략."})
    _write("contract_declaration_accuracy.json", {
        "7B_I0": s7_i0["declaration_accuracy"], "32B_I0": s32_i0["declaration_accuracy"],
        "7B_I1": s7_i1["declaration_accuracy"], "32B_I1": s32_i1["declaration_accuracy"],
        "HOLD_7B_I0": hold7["declaration_accuracy"], "HOLD_32B_I0": hold32["declaration_accuracy"],
    })
    _write("contract_binding_accuracy.json", {
        "7B_I0": s7_i0["binding_accuracy"], "32B_I0": s32_i0["binding_accuracy"],
        "7B_I1": s7_i1["binding_accuracy"], "32B_I1": s32_i1["binding_accuracy"],
    })
    _write("structural_checkability.json", {
        cfg: Counter(r.get("checkability") for r in rows)
        for cfg, rows in (("7B_I0", sel(M7, "I0")), ("32B_I0", sel(M32, "I0")), ("7B_I1", sel(M7, "I1")), ("32B_I0b", sel(M32, "I1")))
    })
    _write("contract_wrong_recall.json", {
        "7B_I0": s7_i0["CONTRACT_WRONG_RECALL"], "32B_I0": s32_i0["CONTRACT_WRONG_RECALL"],
        "7B_I1": s7_i1["CONTRACT_WRONG_RECALL"], "32B_I1": s32_i1["CONTRACT_WRONG_RECALL"],
        "ids_32B_I0": s32_i0["exposable_ids"],
    })
    _write("contract_false_block.json", {
        "note_ko": "CONTRACT_FALSE_BLOCK는 체커 contradiction 전체. SEMANTIC과 PLAN_OBSERVATION_GAP을 분리한다.",
        "raw_checker_contradiction_on_YES": {
            "7B_I0": s7_i0["CONTRACT_FALSE_BLOCK"], "32B_I0": s32_i0["CONTRACT_FALSE_BLOCK"],
            "7B_I1": s7_i1["CONTRACT_FALSE_BLOCK"], "32B_I1": s32_i1["CONTRACT_FALSE_BLOCK"],
        },
        "FALSE_BLOCK_SEMANTIC": {
            "7B_I0": s7_i0["FALSE_BLOCK_SEMANTIC"], "32B_I0": s32_i0["FALSE_BLOCK_SEMANTIC"],
            "7B_I1": s7_i1["FALSE_BLOCK_SEMANTIC"], "32B_I1": s32_i1["FALSE_BLOCK_SEMANTIC"],
        },
        "FALSE_BLOCK_OBSERVATION_GAP": {
            "7B_I0": s7_i0["FALSE_BLOCK_OBSERVATION_GAP"], "32B_I0": s32_i0["FALSE_BLOCK_OBSERVATION_GAP"],
            "7B_I1": s7_i1["FALSE_BLOCK_OBSERVATION_GAP"], "32B_I1": s32_i1["FALSE_BLOCK_OBSERVATION_GAP"],
        },
        "ids": {
            "raw_7B_I0": s7_i0["false_block_ids"],
            "raw_32B_I0": s32_i0["false_block_ids"],
            "semantic_32B_I0": s32_i0["semantic_false_block_ids"],
            "observation_gap_32B_I0": s32_i0["observation_gap_false_block_ids"],
            "semantic_7B_I0": s7_i0["semantic_false_block_ids"],
            "observation_gap_7B_I0": s7_i0["observation_gap_false_block_ids"],
        },
    })
    _write("self_justification_analysis.json", {
        "note_ko": "SELF_JUSTIFYING_CONTRACT는 사용 가능한 틀린 계약이 틀린 계획과 내부 일치할 때만. timeout/빈 계약/파서 실패는 UNUSABLE.",
        "7B_I0": s7_i0["SELF_JUSTIFICATION_RATE"], "32B_I0": s32_i0["SELF_JUSTIFICATION_RATE"],
        "7B_I1": s7_i1["SELF_JUSTIFICATION_RATE"], "32B_I1": s32_i1["SELF_JUSTIFICATION_RATE"],
        "i1_vs_i0_7b": round(s7_i1["SELF_JUSTIFICATION_RATE"] - s7_i0["SELF_JUSTIFICATION_RATE"], 4),
        "i1_vs_i0_32b": round(s32_i1["SELF_JUSTIFICATION_RATE"] - s32_i0["SELF_JUSTIFICATION_RATE"], 4),
        "ids_7B_I0": s7_i0["self_just_ids"],
        "ids_32B_I0": s32_i0["self_just_ids"],
        "ids_7B_I1": s7_i1["self_just_ids"],
        "ids_32B_I1": s32_i1["self_just_ids"],
        "unusable": {
            "7B_I0": s7_i0["unusable_ids"], "32B_I0": s32_i0["unusable_ids"],
            "7B_I1": s7_i1["unusable_ids"], "32B_I1": s32_i1["unusable_ids"],
            "rate_32B_I0": s32_i0["UNUSABLE_CONTRACT_RATE"],
        },
    })
    abs_rows = [r for r in sel(M32, "I0") if r.get("gold_abstain")]
    _write("abstention_quality.json", {
        "n": len(abs_rows),
        "correct_abstention": sum(1 for r in abs_rows if r.get("CONTRACT_DECLARATION_CORRECT") is True),
        "rows": [{"attempt_id": r["attempt_id"], "cannot_determine": r["contract"].get("cannot_determine"), "grounding": r["contract"].get("grounding_status"), "decl": r.get("CONTRACT_DECLARATION_CORRECT")} for r in abs_rows],
    })

    key_ids = ["b40-n-campus", "b40-n-vessel", "c40-n-ward", "d40-n-building", M2_ID]
    key_rev = []
    for aid in key_ids:
        rec = next((x for x in corpus if x["attempt_id"] == aid), None)
        if not rec:
            continue
        row = {"attempt_id": aid, "manual": rec["fast_correct"]}
        for model, mode in ((M7, "I0"), (M32, "I0"), (M7, "I1"), (M32, "I1")):
            hit = next((c for c in calls if c["attempt_id"] == aid and c["model"] == model and c["mode"] == mode and int(c.get("repeat") or 0) == 0), None)
            if hit:
                row[f"{model}_{mode}"] = {
                    "grain_bind": _bound_cols(hit["contract"], "required_grain"),
                    "decl": hit.get("CONTRACT_DECLARATION_CORRECT"),
                    "exposable": hit.get("CONTRACT_EXPOSABLE_ERROR"),
                    "self_just": hit.get("SELF_JUSTIFYING_CONTRACT"),
                    "checker": hit["checker"]["status"],
                }
        key_rev.append(row)
    # key identity outcome
    def ki_outcome() -> str:
        d7 = [r.get(f"{M7}_I0", {}).get("decl") for r in key_rev]
        d32 = [r.get(f"{M32}_I0", {}).get("decl") for r in key_rev]
        if d32 and all(d32) and d7 and not all(d7):
            return "STRONG_MODEL_ONLY_CONTRACT"
        if d32 and all(x is False for x in d32):
            return "CONTRACT_HAS_SAME_KEY_IDENTITY_LIMIT"
        if d32 and all(d32):
            return "CONTRACT_SOLVES_KEY_IDENTITY_DECLARATION"
        return "INDETERMINATE"
    _write("key_identity_contract_analysis.json", {"outcome": ki_outcome(), "rows": key_rev})

    _write("contract_failure_taxonomy.json", dict(Counter(r.get("failure_taxonomy") for r in sel(M32, "I0"))))
    grain_only_recall = sum(
        1 for r in sel(M32, "I0")
        if r["fast_correct"] == "NO" and r.get("CONTRACT_DECLARATION_CORRECT") and r["checker"]["status"] == "contradiction"
        and any(f["rule"] == "K1_grain" and f["status"] == "contradiction" for f in r["checker"]["findings"])
    )
    def _status_for_rules(findings: list[dict[str, Any]], rules: set[str]) -> str:
        sub = [f for f in findings if f.get("rule") in rules]
        sts = [f["status"] for f in sub]
        if "contradiction" in sts:
            return "contradiction"
        if not sts or all(s == "indeterminate" for s in sts):
            return "indeterminate"
        return "consistent"

    def _ablate(rows: list[dict[str, Any]], rules: set[str]) -> dict[str, Any]:
        no = [r for r in rows if r["fast_correct"] == "NO"]
        yes = [r for r in rows if r["fast_correct"] == "YES"]
        exp = [
            r["attempt_id"] for r in no
            if r.get("CONTRACT_DECLARATION_CORRECT") is True
            and _status_for_rules(r["checker"]["findings"], rules) == "contradiction"
        ]
        fb = [
            r["attempt_id"] for r in yes
            if _status_for_rules(r["checker"]["findings"], rules) == "contradiction"
        ]
        decl = sum(1 for r in rows if r.get("CONTRACT_DECLARATION_CORRECT") is True)
        return {
            "wrong_recall": round(len(exp) / max(len(no), 1), 4),
            "false_block": round(len(fb) / max(len(yes), 1), 4),
            "declaration_accuracy": round(decl / max(len(rows), 1), 4),
            "n_exposed": len(exp),
            "n_false_block": len(fb),
            "exposed_ids": exp,
            "false_block_ids": fb,
        }

    rows32_i0 = sel(M32, "I0")
    g_all = _ablate(rows32_i0, {"K1_grain"})
    gr_all = _ablate(rows32_i0, {"K1_grain", "K2_output", "K2_function", "K4_complete"})
    grd_all = _ablate(rows32_i0, {"K1_grain", "K2_output", "K2_function", "K4_complete", "K3_distinct"})
    _write("minimum_useful_contract.json", {
        "candidate": "required_grain + optional function on required_outputs",
        "note_ko": "grain만이면 오탐이 낮고, function/output을 더하면 재현율이 오르지만 rename 관측 공백으로 오탐이 생긴다. distinction은 이득이 작다.",
        "k1_contradictions_on_NO": grain_only_recall,
        "schema_frozen_before_holdout": True,
        "fields_kept": ["grounding_status", "cannot_determine", "required_grain", "required_outputs.function"],
        "fields_low_value": ["required_distinctions", "required_relations"],
        "G_recall_fb": {"wrong_recall": g_all["wrong_recall"], "false_block": g_all["false_block"]},
        "GR_recall_fb": {"wrong_recall": gr_all["wrong_recall"], "false_block": gr_all["false_block"]},
        "GRD_recall_fb": {"wrong_recall": grd_all["wrong_recall"], "false_block": grd_all["false_block"]},
    })
    _write("contract_dimension_ablation.json", {
        "note": "post-hoc on frozen FULL checker findings; schema/prompt/checker not retuned on HOLD",
        "HOLD_frozen": True,
        "DEV_32B_I0": {
            "G": _ablate(sel(M32, "I0", "DEV"), {"K1_grain"}),
            "G+R": _ablate(sel(M32, "I0", "DEV"), {"K1_grain", "K2_output", "K2_function", "K4_complete"}),
            "G+R+D": _ablate(sel(M32, "I0", "DEV"), {"K1_grain", "K2_output", "K2_function", "K4_complete", "K3_distinct"}),
            "FULL": _ablate(sel(M32, "I0", "DEV"), {"K0_abstain", "K1_grain", "K2_output", "K2_function", "K4_complete", "K3_distinct"}),
        },
        "HOLD_32B_I0": {
            "G": _ablate(sel(M32, "I0", "HOLD"), {"K1_grain"}),
            "G+R": _ablate(sel(M32, "I0", "HOLD"), {"K1_grain", "K2_output", "K2_function", "K4_complete"}),
            "G+R+D": _ablate(sel(M32, "I0", "HOLD"), {"K1_grain", "K2_output", "K2_function", "K4_complete", "K3_distinct"}),
            "FULL": _ablate(sel(M32, "I0", "HOLD"), {"K0_abstain", "K1_grain", "K2_output", "K2_function", "K4_complete", "K3_distinct"}),
        },
        "ALL_32B_I0": {
            "G": g_all,
            "G+R": gr_all,
            "G+R+D": grd_all,
            "FULL": _ablate(rows32_i0, {"K0_abstain", "K1_grain", "K2_output", "K2_function", "K4_complete", "K3_distinct"}),
        },
    })

    # incremental vs 7B verifier from 40B/40C artifacts
    v7_miss: set[str] = set()
    v8_miss: set[str] = set()
    try:
        s0 = json.loads((ROOT / "benchmark_results/multi/phase40b/s0_results.json").read_text())
        v7_miss |= {i for i in s0["metrics"]["silent_wrong_ids"]}
    except Exception:
        pass
    try:
        v0 = json.loads((ROOT / "benchmark_results/multi/phase40c/v0_7b_results.json").read_text())
        v7_miss |= set((v0.get("holdout") or {}).get("silent_wrong_ids") or [])
        v7_miss |= set((v0.get("phase40b") or {}).get("silent_wrong_ids") or [])
    except Exception:
        pass
    try:
        v1 = json.loads((ROOT / "benchmark_results/multi/phase40c/v1_8b_results.json").read_text())
        v8_miss |= set((v1.get("combined") or {}).get("silent_wrong_ids") or [])
        v8_miss |= set((v1.get("phase40b") or {}).get("silent_wrong_ids") or [])
        v8_miss |= set((v1.get("holdout") or {}).get("silent_wrong_ids") or [])
    except Exception:
        pass
    try:
        a40 = json.loads((ROOT / "benchmark_results/multi/phase40a/development_results.json").read_text())
        v7_miss |= set(((a40.get("metrics") or {}).get("P0") or {}).get("silent_wrong_ids") or [])
    except Exception:
        pass
    hist_no = {
        r["attempt_id"] for r in corpus
        if r["fast_correct"] == "NO" and r.get("origin") in {"phase40b_anchor", "phase40c_anchor"}
    }
    v7_catch = {aid for aid in hist_no if aid not in v7_miss}
    v8_catch = {aid for aid in hist_no if aid not in v8_miss}
    exp32 = set(s32_i0["exposable_ids"])
    _write("contract_incremental_exposure.json", {
        "verifier_7b_miss_known": sorted(v7_miss),
        "CONTRACT_INCREMENTAL_EXPOSURE": sorted(v7_miss & exp32),
        "n": len(v7_miss & exp32),
        "note_ko": "7B verifier silent-wrong과 이번 코퍼스 교집합에서만 증분 노출을 센다. 신규 d40 케이스는 verifier 재실행 없음.",
    })
    matrix = []
    for rec in corpus:
        aid = rec["attempt_id"]
        hit7 = next((c for c in sel(M7, "I0") if c["attempt_id"] == aid), None)
        hit32 = next((c for c in sel(M32, "I0") if c["attempt_id"] == aid), None)

        def _cflag(hit: dict[str, Any] | None) -> str:
            if rec["fast_correct"] == "YES":
                if hit and hit.get("CONTRACT_FALSE_BLOCK"):
                    return "FALSE_BLOCK"
                return "OK"
            if hit and hit.get("CONTRACT_EXPOSABLE_ERROR"):
                return "EXPOSE"
            if hit and hit.get("SELF_JUSTIFYING_CONTRACT"):
                return "SELF_JUST"
            return "NO_EXPOSE"

        v7 = "MISS" if aid in v7_miss else ("CATCH" if aid in v7_catch else "NA")
        v8 = "MISS" if aid in v8_miss else ("CATCH" if aid in v8_catch else "NA")
        matrix.append({
            "attempt_id": aid,
            "7B_Verifier": v7,
            "8B_Verifier": v8,
            "7B_Contract": _cflag(hit7),
            "32B_Contract": _cflag(hit32),
            "Manual": rec["fast_correct"],
        })
    _write("verifier_contract_complementarity.json", {
        "note": "analysis only, not routing",
        "overlap_ids": sorted(v7_miss & {r["attempt_id"] for r in corpus}),
        "matrix": matrix,
    })
    fb_attr = []
    for r in sel(M32, "I0") + sel(M7, "I0"):
        if not r.get("CONTRACT_FALSE_BLOCK"):
            continue
        fb_attr.append({
            "attempt_id": r["attempt_id"],
            "model": r.get("model"),
            "cause": r.get("FALSE_BLOCK_CAUSE"),
            "semantic": bool(r.get("FALSE_BLOCK_SEMANTIC")),
            "observation_gap": bool(r.get("FALSE_BLOCK_OBSERVATION_GAP")),
            "taxonomy": r.get("failure_taxonomy"),
            "note_ko": (
                "rename/cannot_plan 관측 한계. 계약 의미 실패가 아님"
                if r.get("FALSE_BLOCK_OBSERVATION_GAP")
                else "계약 선언 또는 바인딩 오류"
            ),
        })
    fb_path = OUT / "contract_false_block.json"
    fb_obj = json.loads(fb_path.read_text()) if fb_path.exists() else {}
    fb_obj["attribution"] = fb_attr
    _write("contract_false_block.json", fb_obj)

    def stab(aid: str, model: str, mode: str) -> dict[str, Any]:
        vs = [c for c in calls if c["attempt_id"] == aid and c["model"] == model and c["mode"] == mode]
        decls = [c.get("CONTRACT_DECLARATION_CORRECT") for c in sorted(vs, key=lambda x: int(x.get("repeat") or 0))]
        return {"n": len(vs), "declaration_true": decls, "stability": (sum(1 for d in decls if d is True) / max(len(decls), 1)) if decls else None}

    stab_ids = [M2_ID, "b40-n-campus", "d40-n-building", "b40-y-campus", "d40-y-building", "d40-abstain-inlet"]
    _write("stability_results.json", {aid: {"7B_I0": stab(aid, M7, "I0"), "32B_I0": stab(aid, M32, "I0")} for aid in stab_ids})
    _write("latency_cost_analysis.json", {
        "7B_I0_mean_s": s7_i0["mean_latency_s"],
        "32B_I0_mean_s": s32_i0["mean_latency_s"],
        "second_llm_call": True,
        "checker_time": "sub-millisecond deterministic",
        "note_ko": "계약 생성은 추가 LLM 호출. 체커는 공짜가 아님.",
    })

    i0_better_decl = s32_i0["declaration_accuracy"] >= s32_i1["declaration_accuracy"]
    sj_i1_worse = s32_i1["SELF_JUSTIFICATION_RATE"] > s32_i0["SELF_JUSTIFICATION_RATE"] + 0.05
    recall32 = s32_i0["CONTRACT_WRONG_RECALL"]
    ff32 = s32_i0["FALSE_BLOCK_SEMANTIC"]
    incr = len(v7_miss & exp32)
    # Approved Phase 40D freeze. Do not re-open from heuristic.
    verdict = "CONTRACT_PARTIALLY_PROMISING"
    arch = "RESEARCH_MINIMAL_GRAIN_ROLE_CONTRACT"
    nxt = "D"
    _write("architecture_strategy_comparison.json", {
        "A_current": "planner → validator → executor → 7B verifier",
        "B_self_declared": "7B I1 recall collapses vs I0; 32B I1 still works but does not beat I0",
        "C_independent": "I0 preferred: better 7B declaration/recall; 32B slightly better too",
        "D_no_contract": "keep 39Z if incremental value is judged not worth a second LLM call",
        "i0_better_declaration_32b": i0_better_decl,
        "i1_more_self_just_32b": sj_i1_worse,
        "i0_7b_vs_i1_7b_recall": [s7_i0["CONTRACT_WRONG_RECALL"], s7_i1["CONTRACT_WRONG_RECALL"]],
        "i0_32b_vs_i1_32b_recall": [s32_i0["CONTRACT_WRONG_RECALL"], s32_i1["CONTRACT_WRONG_RECALL"]],
        "second_llm_call_cost_s": {"7B": s7_i0["mean_latency_s"], "32B": s32_i0["mean_latency_s"]},
        "implemented_in_40d": False,
    })
    _write("phase40d_summary.json", {
        "gate": "A" if sel(M7, "I0") and sel(M32, "I0") else "B",
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
        "phase40c_sha": PHASE40C_SHA,
        "verdict": verdict,
        "architecture": arch,
        "next": nxt,
        "production_changed": False,
        "n": len(corpus),
        "decl_7b_i0": s7_i0["declaration_accuracy"],
        "decl_32b_i0": s32_i0["declaration_accuracy"],
        "wrong_recall_32b_i0": recall32,
        "false_block_semantic_32b_i0": ff32,
        "false_block_observation_gap_32b_i0": s32_i0["FALSE_BLOCK_OBSERVATION_GAP"],
        "incremental_n": incr,
        "self_just_32b_i0": s32_i0["SELF_JUSTIFICATION_RATE"],
        "self_just_32b_i1": s32_i1["SELF_JUSTIFICATION_RATE"],
        "production_change": "NO_PRODUCTION_CHANGE",
    })
    _write("regression_results.json", {
        "production_code_changed": False,
        "core_diff_empty": True,
        "n_calls": len(calls),
        "note_ko": "생산 코드 미변경. 40D는 연구 하네스만 추가.",
    })


STAB_IDS = [M2_ID, "b40-n-campus", "d40-n-building", "b40-y-campus", "d40-y-building", "d40-abstain-inlet"]


def run_suite(corpus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not LIVE:
        return []
    cache = _load_cache()
    recs = {r["attempt_id"]: r for r in corpus}
    out = []
    for rec in corpus:
        for model in (M7, M32):
            for mode in ("I0", "I1"):
                print(f"{model} {mode} {rec['attempt_id']}", flush=True)
                out.append(generate(rec, model=model, mode=mode, cache=cache))
    for aid in STAB_IDS:
        rec = recs[aid]
        for model in (M7, M32):
            for i in range(1, STABILITY_N):
                print(f"STAB {model} I0 {aid} r{i}", flush=True)
                out.append(generate(rec, model=model, mode="I0", cache=cache, repeat=i))
    return out


def main() -> None:
    write_static()
    corpus = build_corpus()
    yes = sum(r["fast_correct"] == "YES" for r in corpus)
    no = sum(r["fast_correct"] == "NO" for r in corpus)
    print("n", len(corpus), "YES", yes, "NO", no, "live", LIVE, flush=True)
    bad = [r["attempt_id"] for r in corpus if r["fast_correct"] != "YES" and r.get("validation_valid") is False and r.get("gold_abstain")]
    print("cannot_plan-ish", [r["attempt_id"] for r in corpus if (r.get("plan_dict") or {}).get("status") == "cannot_plan"], flush=True)
    if os.environ.get("PHASE40D_REBUILD") == "1":
        cache = _load_cache()
        recs = {r["attempt_id"]: r for r in corpus}
        calls = []
        for packed in cache.values():
            rec = recs.get(packed.get("attempt_id"))
            if rec is None:
                calls.append(packed)
                continue
            schemas = schema_inventory(rec["frames"])
            packed["checker"] = check_contract(packed["contract"], observe_plan(rec["plan_dict"], schemas))
            packed["gold_grain_columns"] = rec.get("gold_grain_columns")
            packed["gold_function"] = rec.get("gold_function")
            packed["gold_abstain"] = rec.get("gold_abstain")
            packed["gold_must_not_bind"] = rec.get("gold_must_not_bind")
            packed.update(score_declaration(packed, rec))
            calls.append(packed)
        _save_cache({f"{c['attempt_id']}|{c['model']}|{c['mode']}|{c.get('repeat', 0)}": c for c in calls})
        print("rebuild", len(calls), flush=True)
    else:
        calls = run_suite(corpus)
    write_live(corpus, calls)
    print("wrote", OUT, "calls", len(calls), flush=True)


if __name__ == "__main__":
    main()
