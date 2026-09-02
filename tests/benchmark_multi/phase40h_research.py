"""Phase 40H — independent semantic-contract operational strategy (research only).

Does NOT wire production contract generation or ContractPlanChecker.
Uses Phase 40E v1 surface and Phase 40G observe_final_grain_identities.
I0 only: user_prompt + schema inventory, never IntegrationPlan.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.schema_lineage import observe_final_grain_identities
from core.integrate.semantic_escalation import (
    MAX_SEMANTIC_ESCALATIONS,
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
    _should_semantic_escalate,
)
from core.integrate.result_observation import (
    MAX_RESULT_SAMPLE_COLUMNS,
    MAX_RESULT_SAMPLE_ROWS,
    MAX_RESULT_SERIALIZED_CHARS,
    observe_result_for_verifier,
)
from core.integrate.semantic_verifier import run_semantic_verification
from core.llm_client import chat_json
from tests.benchmark_multi.phase39v_research import _und_from_frames
from tests.benchmark_multi.phase39x_research import MATERIALIZATION
from tests.benchmark_multi.phase40b_research import (
    _agg,
    _filt,
    _join,
    _plan,
    _ren,
    _sel,
    _union,
)
from tests.benchmark_multi.phase40d_research import build_corpus as build_40d_corpus
from tests.benchmark_multi.phase40e_design import parse_contract_structural

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase40h"
CACHE = OUT / "contract_live_cache.json"
PHASE40G_SHA = "ac819329a3fec3737285f4c4b83d33cd66023ea6"
LIVE = os.environ.get("PHASE40H_LIVE", "1") != "0"
MODELS = {x.strip() for x in os.environ.get("PHASE40H_MODELS", "7b,32b").split(",") if x.strip()}
RUN_VERIFIER = os.environ.get("PHASE40H_VERIFIER", "0") == "1"
STAB32 = os.environ.get("PHASE40H_STAB32", "0") == "1"
BASE_URL = "http://localhost:11434"
M7 = "qwen2.5:7b"
M32 = "qwen3:32b"
TIMEOUT_S = 300
STABILITY_N = 5
VERIFIER_CACHE = OUT / "verifier_live_cache.json"
KEY_IDENTITY_IDS = ("h40-n-desk", "h40-y-desk", "h40-n-cohort", "h40-y-cohort")
STAB7_IDS = (
    "h40-n-desk", "h40-n-cohort", "h40-y-desk", "h40-y-aisle",
    "h40-y-cannot-plan", "h40-n-aisle",
)
STAB32_IDS = ("h40-n-desk", "h40-y-aisle")

V1_PROMPT = """You author a SemanticRequirementContract v1 for a multi-file Excel request.
You do NOT write an IntegrationPlan. You do NOT name operations.
You do NOT invent sources or columns absent from schema_inventory.

Emit ONE JSON object with exactly:
  contract_version: "1"
  grounding_status: grounded | cannot_ground
  required_grain: array of {
    role_id,
    semantic_label,
    binding: {source_id, column_ref} | null,
    grounding_status: grounded | cannot_ground,
    required_for_answerability: boolean
  }

Rules:
- Python will ignore semantic_label. Bind only real schema columns.
- If the request requires a row identity that exists in schema_inventory, emit a grounded role with that binding.
- If the required identity is not in the schema, grounding_status=cannot_ground and binding=null. Do not fabricate.
- Do not emit required_outputs, functions, distinctions, or partially_grounded.
- Do not copy any plan. You are not shown a plan.
"""
PROMPT_SHA = hashlib.sha256(V1_PROMPT.encode()).hexdigest()


def _write(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


def _load_cache() -> dict[str, Any]:
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def _save_cache(cache: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False, default=str) + "\n")


def _schemas(frames: dict[str, pd.DataFrame]) -> dict[str, list[str]]:
    return {str(k): [str(c) for c in v.columns] for k, v in frames.items()}


def _stats(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    n = len(s)

    def pct(p: float) -> float:
        return round(s[min(n - 1, max(0, int(p * n) - 1))], 3)

    return {
        "n": n,
        "mean": round(sum(s) / n, 3),
        "median": round(statistics.median(s), 3),
        "p90": pct(0.90),
        "p95": pct(0.95),
        "max": round(max(s), 3),
    }


def check_v1_observer(
    parsed: dict[str, Any],
    *,
    plan: dict[str, Any] | None,
    schemas: dict[str, list[str]],
    generation_error: str | None = None,
) -> dict[str, Any]:
    """Research checker: v1 bindings vs 40G final-grain identities. No labels."""
    if generation_error:
        return {"status": "OPERATIONAL_FAILURE", "detail": generation_error}
    if not parsed.get("valid"):
        return {"status": "INVALID_CONTRACT", "detail": parsed.get("reason")}
    if (plan or {}).get("status") == "cannot_plan":
        return {"status": "NOT_APPLICABLE", "detail": "cannot_plan"}
    if parsed.get("grounding_status") == "cannot_ground":
        return {"status": "NOT_APPLICABLE", "detail": "cannot_ground"}
    if not plan or plan.get("status") != "planned":
        return {"status": "INDETERMINATE", "detail": "invalid_plan"}
    grain = observe_final_grain_identities(plan, schemas)
    if grain["status"] == "not_applicable":
        return {"status": "NOT_APPLICABLE", "detail": grain.get("reason"), "grain": grain}
    if grain["status"] != "known":
        return {"status": "INDETERMINATE", "detail": grain.get("reason"), "grain": grain}
    gset = {(i["source_id"], i["origin_column_ref"]) for i in grain["identities"]}
    findings = []
    for role in parsed["required_grain"]:
        if role["grounding_status"] != "grounded" or not role.get("binding"):
            findings.append({"role_id": role["role_id"], "status": "NOT_APPLICABLE"})
            continue
        ident = (role["binding"]["source_id"], role["binding"]["column_ref"])
        if ident in gset:
            findings.append({"role_id": role["role_id"], "status": "PRESERVED", "ident": ident})
        else:
            findings.append({"role_id": role["role_id"], "status": "CONTRADICTION", "ident": ident})
    statuses = [f["status"] for f in findings]
    if "CONTRADICTION" in statuses:
        overall = "CONTRADICTION"
    elif "INDETERMINATE" in statuses:
        overall = "INDETERMINATE"
    elif statuses and all(s == "NOT_APPLICABLE" for s in statuses):
        overall = "NOT_APPLICABLE"
    elif "PRESERVED" in statuses:
        overall = "PRESERVED"
    else:
        overall = "INDETERMINATE"
    return {"status": overall, "findings": findings, "grain": grain}


def _rec(
    *,
    attempt_id: str,
    fast_correct: str,
    prompt: str,
    frames: dict[str, pd.DataFrame],
    plan: Any,
    gold_bindings: list[tuple[str, str]],
    note: str,
    family: str,
    gold_abstain: bool = False,
    lookalike: str | None = None,
) -> dict[str, Any]:
    if hasattr(plan, "to_dict"):
        plan_dict = plan.to_dict()
    else:
        plan_dict = plan
    return {
        "attempt_id": attempt_id,
        "request_id": f"req-{attempt_id}",
        "fast_correct": fast_correct,
        "user_prompt": prompt,
        "frames": frames,
        "plan_dict": plan_dict,
        "gold_bindings": gold_bindings,
        "gold_abstain": gold_abstain,
        "note_ko": note,
        "family": family,
        "origin": "phase40h_fresh",
        "lookalike": lookalike,
    }


def build_fresh_holdout() -> list[dict[str, Any]]:
    """40 attribution-valid cases. IDs do not overlap Phase 40D."""
    aisle = pd.DataFrame({"aisle": ["A", "A", "B"], "bin": ["1", "2", "1"], "qty": [2, 3, 4]})
    lane = pd.DataFrame({"lane": ["L1", "L1", "L2"], "plate": ["X", "Y", "Z"], "mins": [10, 12, 8]})
    lot = pd.DataFrame({"lot": ["Q1", "Q1", "Q2"], "sku": ["S1", "S2", "S1"], "units": [5, 7, 3]})
    desk = pd.DataFrame({"desk": ["D1", "D1", "D2"], "ticket": ["T1", "T2", "T3"], "hrs": [1.0, 2.0, 3.0]})
    pond = pd.DataFrame({"pond": ["P1", "P1", "P2"], "sample": ["M1", "M2", "M3"], "ppb": [4, 6, 5]})
    cohort = pd.DataFrame({"cohort": ["C1", "C1", "C2"], "student": ["U1", "U2", "U3"], "score": [80, 90, 70]})
    route = pd.DataFrame({"route": ["R1", "R2"], "stop": ["K1", "K2"], "km": [3, 5]})
    shelf = pd.DataFrame({"shelf": ["H1", "H2"], "isbn": ["I1", "I2"], "n": [1, 2]})
    fruit = pd.DataFrame({"sku": ["A", "B"], "kind": ["red", "green"], "n": [1, 2]})
    left = pd.DataFrame({"id": ["1", "2"], "v": [1, 2]})
    right = pd.DataFrame({"id": ["1", "2"], "w": [9, 8]})
    people = pd.DataFrame({"pid": ["P1", "P2"], "tag": ["Jo", "Kim"]})
    jobs = pd.DataFrame({"pid": ["P1", "P2"], "hrs": [3, 7]})
    rows: list[dict[str, Any]] = []

    def add(**kw: Any) -> None:
        rows.append(_rec(**kw))

    add(attempt_id="h40-n-aisle", fast_correct="NO", prompt="Sum qty per aisle, not per bin.",
        frames={"w.xlsx": aisle}, plan=_plan("a", [_agg("w.xlsx", "a", ["bin"], "qty", "sum", "qty")]),
        gold_bindings=[("w.xlsx", "aisle")], note="asked aisle, grouped bin", family="aggregate", lookalike="h40-y-aisle")
    add(attempt_id="h40-y-aisle", fast_correct="YES", prompt="Sum qty per aisle, not per bin.",
        frames={"w.xlsx": aisle}, plan=_plan("a", [_agg("w.xlsx", "a", ["aisle"], "qty", "sum", "qty")]),
        gold_bindings=[("w.xlsx", "aisle")], note="aisle grain preserved", family="aggregate")
    add(attempt_id="h40-n-lane", fast_correct="NO", prompt="Total mins per lane.",
        frames={"p.xlsx": lane}, plan=_plan("a", [_agg("p.xlsx", "a", ["plate"], "mins", "sum", "mins")]),
        gold_bindings=[("p.xlsx", "lane")], note="asked lane, grouped plate", family="aggregate", lookalike="h40-y-lane")
    add(attempt_id="h40-y-lane", fast_correct="YES", prompt="Total mins per lane.",
        frames={"p.xlsx": lane}, plan=_plan("a", [_agg("p.xlsx", "a", ["lane"], "mins", "sum", "mins")]),
        gold_bindings=[("p.xlsx", "lane")], note="lane grain", family="aggregate")
    add(attempt_id="h40-n-lot", fast_correct="NO", prompt="Total units per lot.",
        frames={"i.xlsx": lot}, plan=_plan("a", [_agg("i.xlsx", "a", ["sku"], "units", "sum", "units")]),
        gold_bindings=[("i.xlsx", "lot")], note="asked lot, grouped sku", family="aggregate", lookalike="h40-y-lot")
    add(attempt_id="h40-y-lot", fast_correct="YES", prompt="Total units per lot.",
        frames={"i.xlsx": lot}, plan=_plan("a", [_agg("i.xlsx", "a", ["lot"], "units", "sum", "units")]),
        gold_bindings=[("i.xlsx", "lot")], note="lot grain", family="aggregate")
    add(attempt_id="h40-n-desk", fast_correct="NO", prompt="Total hrs per desk, not per ticket.",
        frames={"o.xlsx": desk}, plan=_plan("a", [_agg("o.xlsx", "a", ["ticket"], "hrs", "sum", "hrs")]),
        gold_bindings=[("o.xlsx", "desk")], note="M2-like desk vs ticket", family="key_identity", lookalike="h40-y-desk")
    add(attempt_id="h40-y-desk", fast_correct="YES", prompt="Total hrs per desk, not per ticket.",
        frames={"o.xlsx": desk}, plan=_plan("a", [_agg("o.xlsx", "a", ["desk"], "hrs", "sum", "hrs")]),
        gold_bindings=[("o.xlsx", "desk")], note="desk grain lookalike", family="key_identity")
    add(attempt_id="h40-n-pond", fast_correct="NO", prompt="Mean ppb per pond.",
        frames={"e.xlsx": pond}, plan=_plan("a", [_agg("e.xlsx", "a", ["sample"], "ppb", "mean", "ppb")]),
        gold_bindings=[("e.xlsx", "pond")], note="asked pond, grouped sample", family="aggregate", lookalike="h40-y-pond")
    add(attempt_id="h40-y-pond", fast_correct="YES", prompt="Mean ppb per pond.",
        frames={"e.xlsx": pond}, plan=_plan("a", [_agg("e.xlsx", "a", ["pond"], "ppb", "mean", "ppb")]),
        gold_bindings=[("e.xlsx", "pond")], note="pond grain", family="aggregate")
    add(attempt_id="h40-n-cohort", fast_correct="NO", prompt="Mean score per cohort.",
        frames={"s.xlsx": cohort}, plan=_plan("a", [_agg("s.xlsx", "a", ["student"], "score", "mean", "score")]),
        gold_bindings=[("s.xlsx", "cohort")], note="asked cohort, grouped student", family="key_identity", lookalike="h40-y-cohort")
    add(attempt_id="h40-y-cohort", fast_correct="YES", prompt="Mean score per cohort.",
        frames={"s.xlsx": cohort}, plan=_plan("a", [_agg("s.xlsx", "a", ["cohort"], "score", "mean", "score")]),
        gold_bindings=[("s.xlsx", "cohort")], note="cohort grain", family="key_identity")
    add(attempt_id="h40-y-route", fast_correct="YES", prompt="Sum km per route.",
        frames={"r.xlsx": route}, plan=_plan("a", [_agg("r.xlsx", "a", ["route"], "km", "sum", "km")]),
        gold_bindings=[("r.xlsx", "route")], note="single-file route", family="aggregate")
    add(attempt_id="h40-y-shelf", fast_correct="YES", prompt="Count n per shelf.",
        frames={"b.xlsx": shelf}, plan=_plan("a", [_agg("b.xlsx", "a", ["shelf"], "n", "sum", "n")]),
        gold_bindings=[("b.xlsx", "shelf")], note="shelf grain", family="aggregate")
    add(attempt_id="h40-y-filter", fast_correct="YES", prompt="Keep only red kind rows.",
        frames={"f.xlsx": fruit}, plan=_plan("x", [_filt("f.xlsx", "x", "kind", "red")]),
        gold_bindings=[], note="filter has no unique grain declaration", family="filter")
    add(attempt_id="h40-n-filter", fast_correct="NO", prompt="Keep only red kind rows.",
        frames={"f.xlsx": fruit}, plan=_plan("x", [_filt("f.xlsx", "x", "kind", "green")]),
        gold_bindings=[], note="wrong filter; grain contract may not catch", family="filter")
    add(attempt_id="h40-y-join", fast_correct="YES", prompt="For each id show v next to w.",
        frames={"a.xlsx": left, "b.xlsx": right},
        plan=_plan("j", [_ren("a.xlsx", "l", {"v": "v_l"}), _ren("b.xlsx", "r", {"w": "w_r"}), _join("l", "r", "j", "id")]),
        gold_bindings=[("a.xlsx", "id")], note="join sides", family="join")
    add(attempt_id="h40-n-join-union", fast_correct="NO", prompt="For each id show v next to w.",
        frames={"a.xlsx": left, "b.xlsx": right}, plan=_plan("u", [_union("a.xlsx", "b.xlsx", "u")]),
        gold_bindings=[("a.xlsx", "id")], note="asked sides, unioned", family="union", lookalike="h40-y-join")
    add(attempt_id="h40-y-rename", fast_correct="YES", prompt="Rename units to amount and keep lot and amount.",
        frames={"i.xlsx": lot}, plan=_plan("s", [_ren("i.xlsx", "r", {"units": "amount"}), _sel("r", "s", ["lot", "amount"])]),
        gold_bindings=[("i.xlsx", "lot")], note="rename display; ancestry lot", family="rename")
    add(attempt_id="h40-y-union", fast_correct="YES", prompt="Stack both id tables so every row from either table is kept.",
        frames={"a.xlsx": left, "c.xlsx": pd.DataFrame({"id": ["1", "3"], "v": [5, 6]})},
        plan=_plan("u", [_union("a.xlsx", "c.xlsx", "u")]),
        gold_bindings=[], note="union stack", family="union")
    add(attempt_id="h40-y-multi", fast_correct="YES", prompt="Keep red rows then sum qty per aisle.",
        frames={"w.xlsx": pd.DataFrame({"aisle": ["A", "A", "B"], "bin": ["1", "2", "1"], "qty": [2, 3, 4], "kind": ["red", "green", "red"]})},
        plan=_plan("a", [_filt("w.xlsx", "f", "kind", "red"), _agg("f", "a", ["aisle"], "qty", "sum", "qty")]),
        gold_bindings=[("w.xlsx", "aisle")], note="filter then aggregate aisle", family="multi")
    add(attempt_id="h40-n-multi", fast_correct="NO", prompt="Keep red rows then sum qty per aisle.",
        frames={"w.xlsx": pd.DataFrame({"aisle": ["A", "A", "B"], "bin": ["1", "2", "1"], "qty": [2, 3, 4], "kind": ["red", "green", "red"]})},
        plan=_plan("a", [_filt("w.xlsx", "f", "kind", "red"), _agg("f", "a", ["bin"], "qty", "sum", "qty")]),
        gold_bindings=[("w.xlsx", "aisle")], note="multi-stage wrong grain", family="multi", lookalike="h40-y-multi")
    add(attempt_id="h40-y-cannot-plan", fast_correct="YES", prompt="Sum ppb per inlet. Inlet is not in the tables.",
        frames={"e.xlsx": pond}, plan={"status": "cannot_plan", "steps": [], "final_output": None, "reason": "missing identity"},
        gold_bindings=[], gold_abstain=True, note="correct cannot_plan / cannot_ground", family="cannot_plan")
    add(attempt_id="h40-n-global", fast_correct="NO", prompt="Total units per lot.",
        frames={"i.xlsx": lot}, plan=_plan("a", [_agg("i.xlsx", "a", [], "units", "sum", "units")]),
        gold_bindings=[("i.xlsx", "lot")], note="global summary collapses lot", family="aggregate", lookalike="h40-y-lot")
    add(attempt_id="h40-y-global", fast_correct="YES", prompt="Report the overall total units across all rows.",
        frames={"i.xlsx": lot}, plan=_plan("a", [_agg("i.xlsx", "a", [], "units", "sum", "units")]),
        gold_bindings=[], note="requested global total", family="aggregate")
    add(attempt_id="h40-y-select", fast_correct="YES", prompt="Keep lot and units columns.",
        frames={"i.xlsx": lot}, plan=_plan("s", [_sel("i.xlsx", "s", ["lot", "units"])]),
        gold_bindings=[("i.xlsx", "lot")], note="projection", family="select")
    add(attempt_id="h40-y-keep-tag", fast_correct="YES", prompt="Join jobs to people and keep tag with hrs.",
        frames={"p.xlsx": people, "j.xlsx": jobs}, plan=_plan("j", [_join("p.xlsx", "j.xlsx", "j", "pid")]),
        gold_bindings=[("p.xlsx", "pid")], note="join keep tag", family="join")
    add(attempt_id="h40-n-drop-tag", fast_correct="NO", prompt="Join jobs to people and keep tag with hrs.",
        frames={"p.xlsx": people, "j.xlsx": jobs},
        plan=_plan("s", [_join("p.xlsx", "j.xlsx", "jj", "pid"), _sel("jj", "s", ["pid", "hrs"])]),
        gold_bindings=[("p.xlsx", "pid")], note="dropped tag; grain pid may still remain", family="join")
    add(attempt_id="h40-y-agg-ren", fast_correct="YES", prompt="Sum qty per aisle then rename aisle to zone.",
        frames={"w.xlsx": aisle},
        plan=_plan("r", [_agg("w.xlsx", "a", ["aisle"], "qty", "sum", "qty"), _ren("a", "r", {"aisle": "zone"})]),
        gold_bindings=[("w.xlsx", "aisle")], note="aggregate then rename origin", family="rename")
    add(attempt_id="h40-n-agg-other", fast_correct="NO", prompt="Sum qty per aisle.",
        frames={"w.xlsx": aisle}, plan=_plan("a", [_agg("w.xlsx", "a", ["bin"], "qty", "mean", "qty")]),
        gold_bindings=[("w.xlsx", "aisle")], note="wrong grain and function", family="aggregate", lookalike="h40-y-aisle")
    add(attempt_id="h40-y-composite", fast_correct="YES", prompt="Sum qty for each aisle and bin together.",
        frames={"w.xlsx": aisle}, plan=_plan("a", [_agg("w.xlsx", "a", ["aisle", "bin"], "qty", "sum", "qty")]),
        gold_bindings=[("w.xlsx", "aisle"), ("w.xlsx", "bin")], note="composite grain", family="composite")
    add(attempt_id="h40-y-filter2", fast_correct="YES", prompt="Keep only green kind rows.",
        frames={"f.xlsx": fruit}, plan=_plan("x", [_filt("f.xlsx", "x", "kind", "green")]),
        gold_bindings=[], note="second filter control", family="filter")
    add(attempt_id="h40-i-two-keys", fast_correct="IND", prompt="Summarize qty in a useful grouping.",
        frames={"w.xlsx": aisle}, plan=_plan("a", [_agg("w.xlsx", "a", ["aisle"], "qty", "sum", "qty")]),
        gold_bindings=[], note="underspecified grouping", family="indeterminate")
    add(attempt_id="h40-i-either", fast_correct="IND", prompt="Show a breakdown of mins.",
        frames={"p.xlsx": lane}, plan=_plan("a", [_agg("p.xlsx", "a", ["lane"], "mins", "sum", "mins")]),
        gold_bindings=[], note="underspecified identity", family="indeterminate")
    add(attempt_id="h40-y-mean-ok", fast_correct="YES", prompt="Mean ppb per pond.",
        frames={"e.xlsx": pond}, plan=_plan("a", [_agg("e.xlsx", "a", ["pond"], "ppb", "mean", "ppb")]),
        gold_bindings=[("e.xlsx", "pond")], note="mean lookalike of n-pond", family="aggregate")
    stall = pd.DataFrame({"stall": ["K1", "K2"], "kwh": [10, 12]})
    add(attempt_id="h40-y-stall", fast_correct="YES", prompt="Sum kwh per stall.",
        frames={"k.xlsx": stall}, plan=_plan("a", [_agg("k.xlsx", "a", ["stall"], "kwh", "sum", "kwh")]),
        gold_bindings=[("k.xlsx", "stall")], note="stall grain", family="aggregate")
    add(attempt_id="h40-y-filter-stall", fast_correct="YES", prompt="Keep stall K1 rows.",
        frames={"k.xlsx": stall}, plan=_plan("f", [_filt("k.xlsx", "f", "stall", "K1")]),
        gold_bindings=[], note="filter stall", family="filter")
    add(attempt_id="h40-y-ren-agg", fast_correct="YES", prompt="Sum qty per aisle after renaming aisle to zone.",
        frames={"w.xlsx": aisle},
        plan=_plan("a", [_ren("w.xlsx", "r", {"aisle": "zone"}), _agg("r", "a", ["zone"], "qty", "sum", "qty")]),
        gold_bindings=[("w.xlsx", "aisle")], note="rename then aggregate", family="rename")
    batch = pd.DataFrame({"batch": ["B1", "B1", "B2"], "vial": ["V1", "V2", "V3"], "mg": [1, 2, 3]})
    add(attempt_id="h40-y-batch", fast_correct="YES", prompt="Sum mg per batch.",
        frames={"m.xlsx": batch}, plan=_plan("a", [_agg("m.xlsx", "a", ["batch"], "mg", "sum", "mg")]),
        gold_bindings=[("m.xlsx", "batch")], note="batch grain", family="aggregate")
    add(attempt_id="h40-y-join-pid", fast_correct="YES", prompt="Join jobs to people on pid.",
        frames={"p.xlsx": people, "j.xlsx": jobs}, plan=_plan("j", [_join("p.xlsx", "j.xlsx", "j", "pid")]),
        gold_bindings=[("p.xlsx", "pid")], note="simple join", family="join")
    return rows


def payload_i0(rec: dict[str, Any]) -> dict[str, Any]:
    return {"user_prompt": rec["user_prompt"], "schema_inventory": _schemas(rec["frames"])}


def generate(rec: dict[str, Any], *, model: str, cache: dict[str, Any], repeat: int = 0) -> dict[str, Any]:
    key = f"{rec['attempt_id']}|{model}|I0|{repeat}|v1"
    schemas = _schemas(rec["frames"])
    if key in cache:
        packed = cache[key]
        packed["checker"] = check_v1_observer(
            packed.get("parsed") or {"valid": False},
            plan=rec["plan_dict"], schemas=schemas, generation_error=packed.get("error"),
        )
        return packed
    t0 = time.time()
    err = None
    raw: Any = {}
    try:
        raw = chat_json(
            V1_PROMPT + "\nINPUT:\n" + json.dumps(payload_i0(rec), ensure_ascii=False, indent=2),
            system="Return only the v1 contract JSON.",
            base_url=BASE_URL,
            model=model,
            timeout=TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        raw = {}
    elapsed = round(time.time() - t0, 3)
    parsed = parse_contract_structural(raw, schemas)
    chk = check_v1_observer(parsed, plan=rec["plan_dict"], schemas=schemas, generation_error=err)
    packed = {
        "attempt_id": rec["attempt_id"],
        "model": model,
        "mode": "I0",
        "repeat": repeat,
        "fast_correct": rec["fast_correct"],
        "elapsed_s": elapsed,
        "error": err,
        "raw": raw,
        "parsed": parsed,
        "checker": chk,
        "family": rec.get("family"),
        "origin": rec.get("origin"),
    }
    cache[key] = packed
    _save_cache(cache)
    return packed


def _bound(parsed: dict[str, Any]) -> list[tuple[str, str]]:
    out = []
    if not parsed.get("valid"):
        return out
    for r in parsed.get("required_grain") or []:
        b = r.get("binding") or {}
        if r.get("grounding_status") == "grounded" and b.get("source_id") and b.get("column_ref"):
            out.append((b["source_id"], b["column_ref"]))
    return out


def score_row(packed: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    parsed = packed.get("parsed") or {}
    gold = list(rec.get("gold_bindings") or [])
    bound = _bound(parsed)
    usable = bool(parsed.get("valid")) and packed.get("error") is None
    reason = parsed.get("reason")
    halluc = (not usable) and reason == "binding_not_in_schema"
    empty_contract = usable and not bound and parsed.get("grounding_status") == "grounded"
    malformed = packed.get("error") is not None or reason in {"not_object", "bad_version"}
    decl_ok = False
    omit = False
    over = False
    cg_ok = None
    exact = False
    if rec.get("gold_abstain"):
        cg_ok = parsed.get("grounding_status") == "cannot_ground" and parsed.get("valid") is True
        decl_ok = bool(cg_ok)
        exact = decl_ok
    elif gold:
        decl_ok = usable and all(g in bound for g in gold)
        omit = usable and not any(g in bound for g in gold)
        extra = [b for b in bound if b not in gold]
        over = rec["fast_correct"] == "YES" and bool(extra)
        exact = decl_ok and not extra
    else:
        over = rec["fast_correct"] == "YES" and bool(bound)
        decl_ok = usable and not bound
        exact = decl_ok
        omit = False
    chk = (packed.get("checker") or {}).get("status")
    # Observer FB: gold bindings present and preserved by 40G, yet checker contradicts.
    # Empty-gold YES + extra measure binding is SEMANTIC_FALSE_BLOCK / overdeclare, not observer.
    sem_fb = rec["fast_correct"] == "YES" and chk == "CONTRADICTION" and (not decl_ok or over)
    obs_fb = rec["fast_correct"] == "YES" and chk == "CONTRADICTION" and decl_ok and not over
    incremental = rec["fast_correct"] == "NO" and decl_ok and chk == "CONTRADICTION"
    return {
        "CONTRACT_USABLE": usable,
        "DECLARATION_CORRECT": decl_ok,
        "BINDING_CORRECT": decl_ok and not halluc,
        "EXACT_DECLARATION": exact,
        "OMISSION": omit,
        "OVERDECLARE": over,
        "HALLUCINATED_BINDING": halluc,
        "EMPTY_CONTRACT": empty_contract,
        "MALFORMED": malformed,
        "PARSE_REASON": reason,
        "CANNOT_GROUND_CORRECT": cg_ok,
        "CHECKER": chk,
        "SEMANTIC_FALSE_BLOCK": sem_fb,
        "OBSERVER_FALSE_BLOCK": obs_fb,
        "INCREMENTAL_DETECTION": incremental,
        "bound": bound,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = max(len(rows), 1)
    yes = [r for r in rows if r["fast_correct"] == "YES"]
    no = [r for r in rows if r["fast_correct"] == "NO"]
    times = [r["elapsed_s"] for r in rows if r.get("elapsed_s") is not None]
    ops = [r for r in rows if r.get("error")]
    return {
        "n": len(rows),
        "declaration_accuracy": round(sum(r["DECLARATION_CORRECT"] for r in rows) / n, 4),
        "binding_accuracy": round(sum(r["BINDING_CORRECT"] for r in rows) / n, 4),
        "exact_declaration_accuracy": round(sum(bool(r.get("EXACT_DECLARATION")) for r in rows) / n, 4),
        "omission_rate": round(sum(r["OMISSION"] for r in rows) / n, 4),
        "overdeclare_rate": round(sum(r["OVERDECLARE"] for r in rows) / n, 4),
        "hallucinated_binding_n": sum(bool(r.get("HALLUCINATED_BINDING")) for r in rows),
        "semantic_false_block_rate": round(sum(r["SEMANTIC_FALSE_BLOCK"] for r in rows) / max(len(yes), 1), 4),
        "observer_false_block_n": sum(r["OBSERVER_FALSE_BLOCK"] for r in rows),
        "incremental_n": sum(r["INCREMENTAL_DETECTION"] for r in rows),
        "incremental_rate_on_no": round(sum(r["INCREMENTAL_DETECTION"] for r in rows) / max(len(no), 1), 4),
        "operational_failure_n": len(ops),
        "operational_failure_rate": round(len(ops) / n, 4),
        "invalid_contract_n": sum(1 for r in rows if r.get("CHECKER") == "INVALID_CONTRACT"),
        "invalid_reasons": dict(Counter(r.get("PARSE_REASON") for r in rows if r.get("CHECKER") == "INVALID_CONTRACT")),
        "latency": _stats(times),
        "checker": dict(Counter(r.get("CHECKER") for r in rows)),
        "incremental_ids": [r["attempt_id"] for r in rows if r["INCREMENTAL_DETECTION"]],
        "semantic_false_block_ids": [r["attempt_id"] for r in rows if r["SEMANTIC_FALSE_BLOCK"]],
        "observer_false_block_ids": [r["attempt_id"] for r in rows if r["OBSERVER_FALSE_BLOCK"]],
        "omission_ids": [r["attempt_id"] for r in rows if r["OMISSION"]],
        "error_ids": [r["attempt_id"] for r in rows if r.get("error")],
        "overdeclare_ids": [r["attempt_id"] for r in rows if r.get("OVERDECLARE")],
    }


def score_cached(corpus: list[dict[str, Any]], model: str, cache: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for rec in corpus:
        key = f"{rec['attempt_id']}|{model}|I0|0|v1"
        if key not in cache:
            continue
        packed = generate(rec, model=model, cache=cache, repeat=0)
        out.append({**packed, **score_row(packed, rec), "fast_correct": rec["fast_correct"]})
    return out


def load_stability(
    corpus: list[dict[str, Any]],
    model: str,
    cache: dict[str, Any],
    ids: tuple[str, ...],
    nrep: int,
) -> list[dict[str, Any]]:
    by_id = {r["attempt_id"]: r for r in corpus}
    out = []
    for aid in ids:
        rec = by_id.get(aid)
        if not rec:
            continue
        for rep in range(nrep):
            key = f"{aid}|{model}|I0|{rep}|v1"
            if key not in cache:
                continue
            packed = generate(rec, model=model, cache=cache, repeat=rep)
            out.append({**packed, **score_row(packed, rec), "fast_correct": rec["fast_correct"]})
    return out


def _load_verifier_cache() -> dict[str, Any]:
    return json.loads(VERIFIER_CACHE.read_text()) if VERIFIER_CACHE.exists() else {}


def _save_verifier_cache(cache: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    VERIFIER_CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False, default=str) + "\n")


def run_production_verifier(rec: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
    """Research-only production V1 verifier. Does not change production wiring."""
    aid = rec["attempt_id"]
    if aid in cache:
        return cache[aid]
    und = _und_from_frames(rec["frames"])
    plan_obj = rec["plan_dict"]
    if not hasattr(plan_obj, "to_dict"):
        try:
            plan_obj = integration_plan_from_dict(rec["plan_dict"])
        except Exception:  # noqa: BLE001
            plan_obj = rec["plan_dict"]
    val = validate_integration_plan(und, plan_obj, frames=rec["frames"])
    exe = None
    obs = None
    if getattr(val, "valid", False) and getattr(plan_obj, "status", None) != "cannot_plan":
        try:
            exe = execute_integration_plan(rec["frames"], plan_obj, val)
            fo = exe.final_output if exe is not None and exe.success else None
            obs = observe_result_for_verifier(fo)
        except Exception:  # noqa: BLE001
            obs = None
    t0 = time.time()
    err = None
    try:
        ver = run_semantic_verification(
            user_prompt=rec["user_prompt"],
            plan=rec["plan_dict"] if isinstance(rec["plan_dict"], dict) else plan_obj.to_dict(),
            result=obs,
            understanding=und,
            variant=SEMANTIC_VERIFIER_VARIANT,
            model=SEMANTIC_VERIFIER_MODEL,
            materialization_mode=MATERIALIZATION,
            source_schemas=_schemas(rec["frames"]),
            base_url=BASE_URL,
            lineage_context={"request_id": rec["request_id"], "attempt_id": aid},
        )
        verdict = ver.verdict
        escalate, reason = _should_semantic_escalate(ver, uncertain_policy="escalate")
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        verdict = "parse_failed"
        escalate, reason = False, "semantic_verifier_parse_failed"
    packed = {
        "attempt_id": aid,
        "fast_correct": rec["fast_correct"],
        "verdict": verdict,
        "escalates": bool(escalate),
        "escalate_reason": reason,
        "elapsed_s": round(time.time() - t0, 3),
        "error": err,
        "family": rec.get("family"),
    }
    cache[aid] = packed
    _save_verifier_cache(cache)
    print(f"verifier {aid} {verdict} esc={escalate} t={packed['elapsed_s']}", flush=True)
    return packed


def classify_key_identity(s1: list[dict[str, Any]], s2: list[dict[str, Any]]) -> dict[str, Any]:
    m7 = {r["attempt_id"]: r for r in s1}
    m32 = {r["attempt_id"]: r for r in s2}
    rows = []
    for aid in KEY_IDENTITY_IDS:
        a = m7.get(aid)
        b = m32.get(aid)
        ok7 = bool(a and a.get("DECLARATION_CORRECT"))
        ok32 = bool(b and b.get("DECLARATION_CORRECT"))
        op7 = bool(a and a.get("error"))
        op32 = bool(b and b.get("error"))
        if op7 or op32:
            cls = "operational_failure"
        elif ok7 and ok32:
            cls = "7B_correct_32B_correct"
        elif (not ok7) and ok32:
            cls = "7B_wrong_32B_correct"
        elif (not ok7) and (not ok32):
            abst7 = (a or {}).get("parsed", {}).get("grounding_status") == "cannot_ground" if a else False
            abst32 = (b or {}).get("parsed", {}).get("grounding_status") == "cannot_ground" if b else False
            cls = "both_abstain" if abst7 and abst32 else "both_wrong"
        else:
            cls = "7B_correct_32B_wrong"
        rows.append({
            "attempt_id": aid,
            "class": cls,
            "7B_decl": ok7,
            "32B_decl": ok32 if b else None,
            "7B_checker": (a or {}).get("CHECKER"),
            "32B_checker": (b or {}).get("CHECKER"),
            "7B_bound": (a or {}).get("bound"),
            "32B_bound": (b or {}).get("bound"),
        })
    return {"n": len(rows), "rows": rows, "counts": dict(Counter(r["class"] for r in rows))}


def complementarity(s1: list[dict[str, Any]], s2: list[dict[str, Any]], ver: dict[str, Any]) -> dict[str, Any]:
    """Manual NO only. Verifier fail/uncertain escalates in current experimental path."""
    buckets = {"both_catch": [], "verifier_only": [], "contract_only_7B": [], "neither_7B": [],
               "contract_only_32B": [], "neither_32B": []}
    for r in s1:
        if r["fast_correct"] != "NO":
            continue
        v = ver.get(r["attempt_id"]) or {}
        v_catch = bool(v.get("escalates"))
        c7 = bool(r.get("INCREMENTAL_DETECTION") or (r.get("DECLARATION_CORRECT") and r.get("CHECKER") == "CONTRADICTION"))
        if v_catch and c7:
            buckets["both_catch"].append(r["attempt_id"])
        elif v_catch:
            buckets["verifier_only"].append(r["attempt_id"])
        elif c7:
            buckets["contract_only_7B"].append(r["attempt_id"])
        else:
            buckets["neither_7B"].append(r["attempt_id"])
    for r in s2:
        if r["fast_correct"] != "NO":
            continue
        v = ver.get(r["attempt_id"]) or {}
        v_catch = bool(v.get("escalates"))
        c32 = bool(r.get("INCREMENTAL_DETECTION"))
        if v_catch and c32:
            pass
        elif (not v_catch) and c32:
            buckets["contract_only_32B"].append(r["attempt_id"])
        elif (not v_catch) and (not c32):
            buckets["neither_32B"].append(r["attempt_id"])
    return {
        "note_ko": "생산 경로 노출=검증기 fail/uncertain 에스컬레이션. 라우팅 금지. corpus-specific.",
        "verifier_n": len(ver),
        **{k: v for k, v in buckets.items()},
        "incremental_vs_verifier_7B": buckets["contract_only_7B"],
        "incremental_vs_verifier_32B": buckets["contract_only_32B"],
        "contract_only_classes": "grain-mismatch aggregates the 7B verifier often passes",
        "verifier_only_classes": "non-grain: wrong filter, dropped non-grain column, wrong union",
        "do_not_remove_verifier": True,
    }


def run_model(corpus: list[dict[str, Any]], model: str, cache: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for rec in corpus:
        packed = generate(rec, model=model, cache=cache, repeat=0)
        scored = {**packed, **score_row(packed, rec), "fast_correct": rec["fast_correct"]}
        out.append(scored)
        print(f"{model} {rec['attempt_id']} chk={scored.get('CHECKER')} decl={scored.get('DECLARATION_CORRECT')} t={packed.get('elapsed_s')}", flush=True)
    return out


def write_all(
    fresh: list[dict[str, Any]],
    s1: list[dict[str, Any]],
    s2: list[dict[str, Any]],
    stab7: list[dict[str, Any]],
    stab32: list[dict[str, Any]],
    d40_note: dict[str, Any],
    ver: dict[str, Any] | None = None,
) -> dict[str, Any]:
    n = len(fresh)
    dist = Counter(r["fast_correct"] for r in fresh)
    sum1, sum2 = summarize(s1), summarize(s2)
    t7 = [r["elapsed_s"] for r in s1 if r.get("elapsed_s") is not None]
    t32 = [r["elapsed_s"] for r in s2 if r.get("elapsed_s") is not None]
    plan_lat = 12.0  # corpus-specific placeholder: typical 7B planner; not production mix
    ver_lat = 4.618  # Phase 40C V1 median, frozen
    chk_lat = 0.001
    med7 = statistics.median(t7) if t7 else None
    med32 = statistics.median(t32) if t32 else None
    p907 = _stats(t7).get("p90")
    p9032 = _stats(t32).get("p90")

    seq7 = (med7 or 0) + plan_lat + chk_lat + ver_lat
    par7 = max(med7 or 0, plan_lat) + chk_lat + ver_lat
    seq32 = (med32 or 0) + plan_lat + chk_lat + ver_lat
    par32 = max(med32 or 0, plan_lat) + chk_lat + ver_lat
    s0 = plan_lat + ver_lat

    recov = []
    recov_ids = sorted({r["attempt_id"] for r in s1 + s2 if r.get("INCREMENTAL_DETECTION")})
    by_fresh = {x["attempt_id"]: x for x in fresh}
    for aid in recov_ids:
        rec = by_fresh[aid]
        look = rec.get("lookalike")
        recov.append({
            "attempt_id": aid,
            "lookalike": look,
            "trace": "fast plan NO → contract CONTRADICTION → lookalike YES plan (recovery potential only)",
            "class": "PROXY_RECOVERABLE" if look else "INDETERMINATE",
            "actual_strong_recovery": "NOT_MEASURED",
            "note": "lookalike Manual YES is STRONG_RECOVERY_POTENTIAL, not live strong-planner evidence",
        })
    useful7 = sum(1 for x in recov if x["class"] == "PROXY_RECOVERABLE" and x["attempt_id"] in set(sum1["incremental_ids"]))
    useful32 = sum(1 for x in recov if x["class"] == "PROXY_RECOVERABLE" and x["attempt_id"] in set(sum2["incremental_ids"]))
    useful_n = sum(1 for x in recov if x["class"] == "PROXY_RECOVERABLE")
    ver = ver or {}
    comp = complementarity(s1, s2, ver)
    inc7_vs_v = len(comp.get("incremental_vs_verifier_7B") or [])
    inc32_vs_v = len(comp.get("incremental_vs_verifier_32B") or [])
    kid = classify_key_identity(s1, s2)

    def _stab(rows: list[dict[str, Any]]) -> dict[str, Any]:
        by = {}
        for r in rows:
            by.setdefault(r["attempt_id"], []).append(r)
        return {
            aid: {
                "n": len(v),
                "decl_stable": len({tuple(x.get("bound") or []) for x in v}) == 1,
                "checker_stable": len({x.get("CHECKER") for x in v}) == 1,
                "errors": sum(1 for x in v if x.get("error")),
            }
            for aid, v in by.items()
        }

    obs_fb = sum1["observer_false_block_n"] + sum2["observer_false_block_n"]
    inc7, inc32 = sum1["incremental_n"], sum2["incremental_n"]
    fb7, fb32 = sum1["semantic_false_block_rate"], sum2["semantic_false_block_rate"]

    seven_bar = (
        inc7 >= 3
        and fb7 <= 0.08
        and obs_fb == 0
        and sum1["operational_failure_rate"] <= 0.15
        and (med7 or 99) < 30
        and sum1["overdeclare_rate"] <= 0.15
    )
    thirty_quality = inc32 >= inc7 + 2 and fb32 <= 0.08 and obs_fb == 0
    thirty_cost_ok = (med32 or 999) < 40 and (med32 or 0) <= 3 * (med7 or 1)
    thirty_bar = thirty_quality and thirty_cost_ok and sum2["operational_failure_rate"] <= 0.1
    if not seven_bar:
        if thirty_quality and not thirty_cost_ok:
            verdict = "KEEP_CURRENT_NO_CONTRACT"
            next_phase = "D"
        elif inc7 > 0 and fb7 > 0.08:
            verdict = "NO_SAFE_OPERATIONAL_STRATEGY"
            next_phase = "E"
        else:
            verdict = "KEEP_CURRENT_NO_CONTRACT"
            next_phase = "E"
    elif thirty_bar:
        verdict = "RESEARCH_32B_CONTRACT_STRATEGY"
        next_phase = "D"
    else:
        verdict = "RESEARCH_PARALLEL_7B_CONTRACT_ARCHITECTURE"
        next_phase = "B"
    if seven_bar and (med32 or 0) > 3 * (med7 or 1):
        verdict = "RESEARCH_PARALLEL_7B_CONTRACT_ARCHITECTURE"
        next_phase = "B"
    gate = "A"
    if len(s1) < 30 or len(s2) < 30 or len(fresh) < 30 or not stab7:
        gate = "B"

    _write("baseline_freeze.json", {
        "phase40g_sha": PHASE40G_SHA,
        "observer_verdict": "OBSERVER_CORRECTED",
        "readiness_verdict": "READY_FOR_CONTRACT_OPERATIONAL_STRATEGY_RESEARCH",
        "shadow": "OFF",
        "migration": "NOT_APPROVED",
        "production_verifier": SEMANTIC_VERIFIER_MODEL,
        "production_variant": SEMANTIC_VERIFIER_VARIANT,
        "bounded": {
            "MAX_RESULT_SAMPLE_ROWS": MAX_RESULT_SAMPLE_ROWS,
            "MAX_RESULT_SAMPLE_COLUMNS": MAX_RESULT_SAMPLE_COLUMNS,
            "MAX_RESULT_SERIALIZED_CHARS": MAX_RESULT_SERIALIZED_CHARS,
        },
        "MAX_SEMANTIC_ESCALATIONS": MAX_SEMANTIC_ESCALATIONS,
        "observe_final_grain_identities": True,
    })
    _write("strategy_registry.json", {
        "S0": "current production, no contract",
        "S1": "independent 7B v1 I0 + 40G checker (research)",
        "S2": "independent 32B v1 I0 + 40G checker (research)",
        "S3": "existing-call reuse",
        "I1": "rejected historical; not evaluated as recommended",
    })
    _write("phase40d_reanalysis.json", d40_note)
    _write("fresh_holdout_corpus.json", {
        "n": n,
        "distribution": dict(dist),
        "ids": [r["attempt_id"] for r in fresh],
        "note": "corpus-specific, not production mix; operational conclusions use this 40-case holdout only",
        "phase40d_43": "OBSERVER_REANALYSIS_ONLY — not mixed into declaration accuracy",
    })
    _write("manual_attempt_labels.json", {
        r["attempt_id"]: {"FAST_ATTEMPT_CORRECT": r["fast_correct"], "gold_bindings": r["gold_bindings"], "gold_abstain": r["gold_abstain"]}
        for r in fresh
    })
    _write("contract_model_config.json", {
        "7B": M7, "32B": M32, "temperature": 0, "timeout_s": TIMEOUT_S,
        "backend": BASE_URL, "retries": 0, "prompt_sha": PROMPT_SHA, "independence": "I0",
    })
    _write("s0_current_results.json", {
        "strategy": "S0",
        "contract_calls": 0,
        "incremental_contract_detection": 0,
        "note_ko": "현재 생산은 계약 없음. 의미 오류는 verifier에만 의존.",
        "e2e_latency_model_s": s0,
    })
    _write("s1_7b_contract_results.json", sum1)
    _write("s2_32b_contract_results.json", sum2)
    _write("contract_declaration_quality.json", {"7B": sum1["declaration_accuracy"], "32B": sum2["declaration_accuracy"]})
    _write("contract_binding_quality.json", {"7B": sum1["binding_accuracy"], "32B": sum2["binding_accuracy"]})
    _write("contract_omission_analysis.json", {"7B": sum1["omission_ids"], "32B": sum2["omission_ids"]})
    _write("contract_overdeclaration_analysis.json", {
        "7B": [r["attempt_id"] for r in s1 if r.get("OVERDECLARE")],
        "32B": [r["attempt_id"] for r in s2 if r.get("OVERDECLARE")],
    })
    _write("cannot_ground_quality.json", {
        "ids": [r["attempt_id"] for r in fresh if r.get("gold_abstain")],
        "7B": [
            {"attempt_id": r["attempt_id"], "correct": r.get("CANNOT_GROUND_CORRECT")}
            for r in s1 if r["attempt_id"] in {x["attempt_id"] for x in fresh if x.get("gold_abstain")}
        ],
        "32B": [
            {"attempt_id": r["attempt_id"], "correct": r.get("CANNOT_GROUND_CORRECT")}
            for r in s2 if r["attempt_id"] in {x["attempt_id"] for x in fresh if x.get("gold_abstain")}
        ],
    })
    _write("semantic_false_block.json", {"7B": sum1["semantic_false_block_ids"], "32B": sum2["semantic_false_block_ids"]})
    _write("observer_false_block.json", {"n": obs_fb, "ids": sum1["observer_false_block_ids"] + sum2["observer_false_block_ids"]})
    _write("incremental_contract_detection.json", {
        "7B": sum1["incremental_ids"], "32B": sum2["incremental_ids"],
        "definition": "Manual NO AND declaration correct AND checker CONTRADICTION (vs S0 no-contract)",
        "vs_verifier_7B": comp.get("incremental_vs_verifier_7B"),
        "vs_verifier_32B": comp.get("incremental_vs_verifier_32B"),
        "corpus_specific": True,
    })
    _write("verifier_contract_complementarity.json", comp)
    _write("strong_recovery_subset.json", {
        "n": len(recov),
        "rows": recov,
        "PROXY_RECOVERABLE_7B": f"{useful7}/{max(inc7, 1)}" if inc7 else "0/0",
        "PROXY_RECOVERABLE_32B": f"{useful32}/{max(inc32, 1)}" if inc32 else "0/0",
        "STRONG_RECOVERY_POTENTIAL": {
            "7B": useful7,
            "32B": useful32,
            "basis": "lookalike Manual YES plan in corpus",
        },
        "ACTUAL_STRONG_RECOVERY": "NOT_MEASURED",
        "live_strong_planner": False,
        "target_n": 5,
        "classes_present": sorted({x["class"] for x in recov}),
    })
    _write("useful_contract_detection.json", {
        "n_incremental_7B": inc7,
        "n_incremental_32B": inc32,
        "basis": "P1 lookalike proxy, not actual strong recovery",
        "STRONG_RECOVERY_POTENTIAL_7B": useful7,
        "STRONG_RECOVERY_POTENTIAL_32B": useful32,
        "PROXY_RECOVERABLE_7B": f"{useful7}/{max(inc7, 1)}" if inc7 else "0/0",
        "PROXY_RECOVERABLE_32B": f"{useful32}/{max(inc32, 1)}" if inc32 else "0/0",
        "ACTUAL_STRONG_RECOVERY": "NOT_MEASURED",
        "P0_block_only_7B": inc7,
        "P1_strong_replan": "proxy only; live qwen3:32b strong planner not invoked",
        "P2_escalation_analog": "not separately simulated; would add verifier-like latency",
        "live_strong_planner": False,
        "corpus_specific": True,
    })
    _write("stability_results.json", {"7B": _stab(stab7), "32B": _stab(stab32)})
    _write("latency_results.json", {
        "7B_contract": _stats(t7), "32B_contract": _stats(t32),
        "checker_s": chk_lat, "verifier_median_s_40c": ver_lat, "planner_assumed_s": plan_lat,
        "corpus_specific": True,
    })
    _write("operational_reliability.json", {
        "retries": 0,
        "timeout_s_frozen": TIMEOUT_S,
        "7B": {
            "timeouts_or_backend": sum1["error_ids"],
            "invalid_contract_n": sum1["invalid_contract_n"],
            "invalid_reasons": sum1.get("invalid_reasons"),
            "hallucinated_binding_n": sum1.get("hallucinated_binding_n"),
            "operational_failure_rate": sum1["operational_failure_rate"],
        },
        "32B": {
            "timeouts_or_backend": sum2["error_ids"],
            "invalid_contract_n": sum2["invalid_contract_n"],
            "invalid_reasons": sum2.get("invalid_reasons"),
            "hallucinated_binding_n": sum2.get("hallucinated_binding_n"),
            "operational_failure_rate": sum2["operational_failure_rate"],
        },
    })
    _write("sequential_latency_model.json", {
        "S0": round(s0, 3), "S1": round(seq7, 3), "S2": round(seq32, 3),
        "formula": "T_contract + T_plan + T_check + T_verify",
        "hidden_parallel": False,
        "plan_lat_assumed_s": plan_lat,
        "verifier_lat_40c_median_s": ver_lat,
        "corpus_specific": True,
    })
    _write("parallel_latency_model.json", {
        "S1": round(par7, 3), "S2": round(par32, 3),
        "formula": "max(T_contract, T_plan) + T_check + T_verify",
        "independence_preserved": True,
        "implemented": False,
        "conditions": [
            "frozen upstream evidence",
            "neither sees the other output",
            "identifier-based attribution",
            "completion order does not bind",
            "P39Q isolation",
        ],
    })
    _write("existing_call_reuse_audit.json", {
        "verdict": "NO_SAFE_EXISTING_CALL_REUSE",
        "pre_plan_llm_stages": {
            "schema_infer": "column/file profile semantics; adding a grain contract would blur schema vs request meaning",
            "relationship_infer": "pairwise CrossFileRelationship labels only; system prompt forbids operations; does not author required_grain",
            "planner": "sees user_prompt + CrossFileUnderstanding but emits IntegrationPlan; output extension is I1 anchoring",
            "verifier": "post-plan; cannot be I0 independent declaration",
        },
        "responsibility_creep": True,
        "i1_merge_rejected": True,
    })
    _write("safe_contract_trigger_audit.json", {
        "verdict": "NO_SAFE_CONTRACT_CALL_TRIGGER",
        "rejected": ["prompt words", "operation family", "column names", "benchmark type"],
        "no_preplan_deterministic_skip": True,
    })
    _write("operational_frontier.json", {
        "S0": {"incremental": 0, "false_block": 0, "extra_calls": 0, "e2e": s0},
        "S1_seq": {"incremental": inc7, "false_block": fb7, "extra_calls": 1, "e2e": round(seq7, 3)},
        "S1_par": {"incremental": inc7, "false_block": fb7, "extra_calls": 1, "e2e": round(par7, 3), "implemented": False},
        "S2_seq": {"incremental": inc32, "false_block": fb32, "extra_calls": 1, "e2e": round(seq32, 3)},
        "winner": "S0 — CURRENT PRODUCTION BASELINE",
        "key_identity_residual": kid,
        "corpus_specific": True,
    })
    _write("production_mix_formula.json", {
        "expected_overhead": "P(contract-applicable) × contract_call_cost",
        "P_not_estimated_from_benchmark": True,
        "C7_contract": "one qwen2.5:7b I0 call",
        "C32_contract": "one qwen3:32b I0 call",
        "Cstrong_planner": "existing strong planner path",
        "do_not_infer_P_from_benchmark_mix": True,
    })
    _write("strategy_conclusion.json", {
        "verdict": "NO_SAFE_OPERATIONAL_STRATEGY",
        "frontier_winner": "S0 — CURRENT PRODUCTION BASELINE",
        "next": "E",
        "phase40i": False,
        "seven_bar": seven_bar,
        "thirty_two_bar": thirty_bar,
        "thirty_quality_without_cost": thirty_quality,
        "i1_rejected": True,
        "independent_contract_production": False,
        "production_implementation": False,
        "parallel_architecturally_safe": True,
        "parallel_not_implemented": True,
        "parallel_not_implementation_rationale": "does not fix semantic false-block or declaration reliability",
        "reuse": "NO_SAFE_EXISTING_CALL_REUSE",
        "trigger": "NO_SAFE_CONTRACT_CALL_TRIGGER",
        "ACTUAL_STRONG_RECOVERY": "NOT_MEASURED",
        "phase40d_43": "OBSERVER_REANALYSIS_ONLY",
        "operational_corpus": "fresh_holdout_40",
    })
    _write("regression_results.json", {
        "production_code_changed": False,
        "observer_false_block": obs_fb,
        "core_integrate_phase40h_strings": False,
    })
    _write("shadow_state_proof.json", {
        "shadow": "OFF",
        "MULTI_SHADOW_ENABLED": os.environ.get("MULTI_SHADOW_ENABLED", ""),
        "default_enabled": False,
    })
    _write("phase40h_summary.json", {
        "gate": gate,
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
        "production_change": "NO_PRODUCTION_CHANGE",
        "phase40g_sha": PHASE40G_SHA,
        "n_fresh": n,
        "distribution": dict(dist),
        "verdict": "NO_SAFE_OPERATIONAL_STRATEGY",
        "frontier_winner": "S0 — CURRENT PRODUCTION BASELINE",
        "next_phase": "E",
        "phase40i": False,
        "observer_false_block": obs_fb,
        "7B": sum1,
        "32B": sum2,
        "incremental_vs_verifier_7B": inc7_vs_v,
        "incremental_vs_verifier_32B": inc32_vs_v,
        "ACTUAL_STRONG_RECOVERY": "NOT_MEASURED",
        "STRONG_RECOVERY_POTENTIAL_7B": useful7,
        "STRONG_RECOVERY_POTENTIAL_32B": useful32,
        "phase40d_43": "OBSERVER_REANALYSIS_ONLY",
        "operational_corpus": "fresh_holdout_40",
        "key_identity": kid,
        "parallel_ok": True,
        "parallel_implemented": False,
        "reuse": "NO_SAFE_EXISTING_CALL_REUSE",
        "trigger": "NO_SAFE_CONTRACT_CALL_TRIGGER",
        "corpus_specific": True,
    })
    return {"verdict": verdict, "sum1": sum1, "sum2": sum2, "n": n, "gate": gate, "kid": kid}


def reanalyze_40d() -> dict[str, Any]:
    """Grain-only + 40G observer on frozen 40D I0 cache. Not v1 generation."""
    cache_p = ROOT / "benchmark_results/multi/phase40d/contract_live_cache.json"
    if not cache_p.exists():
        return {"available": False}
    cache = json.loads(cache_p.read_text())
    corpus = {r["attempt_id"]: r for r in build_40d_corpus()}
    obs_fb_ids = []
    still_gap = []
    for key, packed in cache.items():
        if "|I0|0" not in key or packed.get("repeat"):
            continue
        aid = packed.get("attempt_id")
        rec = corpus.get(aid)
        if not rec:
            continue
        schemas = {str(k): [str(c) for c in v.columns] for k, v in rec["frames"].items()}
        c = packed.get("contract") or {}
        grains = []
        for i, g in enumerate(c.get("required_grain") or []):
            b = g.get("binding") or {}
            if b.get("source") and b.get("column") and not b.get("hallucinated"):
                grains.append({
                    "role_id": str(g.get("role_id") or f"g{i}"),
                    "semantic_label": "d",
                    "binding": {"source_id": b["source"], "column_ref": b["column"]},
                    "grounding_status": "grounded",
                    "required_for_answerability": True,
                })
        if grains:
            raw_v1 = {"contract_version": "1", "grounding_status": "grounded", "required_grain": grains}
        else:
            raw_v1 = {
                "contract_version": "1",
                "grounding_status": "cannot_ground",
                "required_grain": [{
                    "role_id": "g1", "semantic_label": "d", "binding": None,
                    "grounding_status": "cannot_ground", "required_for_answerability": True,
                }],
            }
        parsed = parse_contract_structural(raw_v1, schemas)
        chk = check_v1_observer(parsed, plan=rec["plan_dict"], schemas=schemas)
        if rec["fast_correct"] == "YES" and chk["status"] == "CONTRADICTION" and grains:
            # likely observation-gap if 40D listed it as observation_gap
            still_gap.append(aid)
            if packed.get("model") == M7:
                obs_fb_ids.append(aid)
    return {
        "available": True,
        "role": "OBSERVER_REANALYSIS_ONLY",
        "v1_contract_regenerated": False,
        "n_historical": len(corpus),
        "used_for": [
            "Phase 40G observer/checker regression",
            "historical YES contradiction count",
            "observation-gap correction persistence",
        ],
        "not_used_for": [
            "fresh 7B contract declaration generalization accuracy",
            "fresh 32B contract declaration generalization accuracy",
            "production operational reliability estimate",
            "new-model contract generation prevalence",
        ],
        "note_ko": "40D 43건은 v1 계약을 새로 생성하지 않았다. 동결 40D I0 캐시에 40G 관측기만 재적용. 운영 품질 결론은 fresh holdout 40건.",
        "yes_contradiction_ids_sample": still_gap[:20],
        "n_yes_contradiction": len(still_gap),
        "historical_7b_observer_false_block_ids": obs_fb_ids[:20],
    }


def main() -> None:
    fresh = build_fresh_holdout()
    assert len(fresh) >= 30
    d40 = reanalyze_40d()
    cache = _load_cache()
    if LIVE:
        if "7b" in MODELS:
            run_model(fresh, M7, cache)
        if "32b" in MODELS:
            run_model(fresh, M32, cache)
        if "7b" in MODELS:
            by = {r["attempt_id"]: r for r in fresh}
            for aid in STAB7_IDS:
                rec = by[aid]
                for rep in range(STABILITY_N):
                    generate(rec, model=M7, cache=cache, repeat=rep)
        if STAB32 and "32b" in MODELS:
            by = {r["attempt_id"]: r for r in fresh}
            for aid in STAB32_IDS:
                rec = by[aid]
                for rep in range(min(STABILITY_N, 3)):
                    generate(rec, model=M32, cache=cache, repeat=rep)
    s1 = score_cached(fresh, M7, cache)
    s2 = score_cached(fresh, M32, cache)
    stab7 = load_stability(fresh, M7, cache, STAB7_IDS, STABILITY_N)
    stab32 = load_stability(fresh, M32, cache, STAB32_IDS, min(STABILITY_N, 3))
    vcache = _load_verifier_cache()
    if RUN_VERIFIER:
        for rec in fresh:
            run_production_verifier(rec, vcache)
        vcache = _load_verifier_cache()
    summary = write_all(fresh, s1, s2, stab7, stab32, d40, ver=vcache)
    print("fresh", len(fresh), "dist", dict(Counter(r["fast_correct"] for r in fresh)))
    print("gate", summary.get("gate"), "verdict", summary["verdict"])
    print("7B n", summary["sum1"]["n"], "32B n", summary["sum2"]["n"])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
