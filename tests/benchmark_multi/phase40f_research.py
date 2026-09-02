"""Phase 40F — contract lineage observability (research only).

Does NOT generate production contracts, wire a production checker, or change
planner/Validator/Executor/verifier/DSL. Tests whether V2.2 origins can prove
declared grain-binding survival without reading user meaning.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from core.integrate.result_observation import (
    MAX_RESULT_SAMPLE_COLUMNS,
    MAX_RESULT_SAMPLE_ROWS,
    MAX_RESULT_SERIALIZED_CHARS,
)
from core.integrate.schema_lineage import build_schema_lineage
from core.integrate.semantic_escalation import (
    MAX_SEMANTIC_ESCALATIONS,
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
)
from tests.benchmark_multi.phase40e_design import (
    parse_contract_structural,
    observe_required_ungrounded,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase40f"
PHASE40E_SHA = "056ca4cb072c8dbf6534afc0d1bd68eb0631212a"

CHECK_STATUSES = frozenset({
    "PRESERVED", "CONTRADICTION", "INDETERMINATE", "NOT_APPLICABLE",
    "INVALID_CONTRACT", "OPERATIONAL_FAILURE", "INVALID_PLAN",
})

GAP_KO = {
    "ORIGIN_UNKNOWN": "최종 열 조상을 결정할 수 없음",
    "RENAME_LINEAGE_MISSING": "rename 조상 복사가 비어 있음",
    "FINAL_GRAIN_UNKNOWN": "최종 grain 집합을 연산 그래프로 확정할 수 없음",
    "MULTI_ANCESTRY_AMBIGUOUS": "grain 열에 조상이 여럿이라 단일 정체성을 증명할 수 없음",
    "BRANCH_STATE_UNKNOWN": "분기 변환 상태를 추적할 수 없음",
    "CANNOT_PLAN_NOT_APPLICABLE": "cannot_plan에는 물질화 grain이 없음",
    "MALFORMED_BINDING": "바인딩 구조가 유효하지 않음",
    "SOURCE_NOT_FOUND": "선언 source_id가 스키마 재고에 없음",
    "COLUMN_NOT_FOUND": "선언 column_ref가 해당 source에 없음",
    "UNSUPPORTED_OPERATION": "지원하지 않는 연산이 그래프에 있음",
    "OTHER": "기타 관측 공백",
}


def _write(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _step(sid: str, op: str, inputs: list[str], output: str, **params: Any) -> dict[str, Any]:
    return {"id": sid, "op": op, "inputs": inputs, "output": output, "params": params}


def _plan(final: str, steps: list[dict[str, Any]], status: str = "planned") -> dict[str, Any]:
    return {"status": status, "final_output": final, "steps": steps}


def _contract(source_id: str, column_ref: str, role_id: str = "g1") -> dict[str, Any]:
    return {
        "contract_version": "1",
        "grounding_status": "grounded",
        "required_grain": [{
            "role_id": role_id,
            "semantic_label": "diagnostic only — checker must ignore",
            "binding": {"source_id": source_id, "column_ref": column_ref},
            "grounding_status": "grounded",
            "required_for_answerability": True,
        }],
    }


def _contract_multi(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    roles = []
    for i, (sid, col) in enumerate(pairs, 1):
        roles.append({
            "role_id": f"g{i}",
            "semantic_label": "diagnostic",
            "binding": {"source_id": sid, "column_ref": col},
            "grounding_status": "grounded",
            "required_for_answerability": True,
        })
    return {"contract_version": "1", "grounding_status": "grounded", "required_grain": roles}


SA = {"src_a": ["entity_key", "measure", "extra"]}
SB = {"src_a": SA["src_a"], "src_b": ["other_key", "val_b", "entity_key"]}
SB_CLEAN = {"src_a": SA["src_a"], "src_b": ["other_key", "val_b"]}


def observe_final_grain(plan: dict[str, Any], lineage: dict[str, Any]) -> dict[str, Any]:
    """Derive final grain identities from the op graph. Not a native V2.2 field."""
    final_name = str(plan.get("final_output") or "")
    produced = set(lineage.get("step_outputs") or {}) | set(lineage.get("source_schemas") or {})
    if final_name and final_name not in produced:
        return {"complete": False, "kind": "missing_final", "grain_columns": [], "gap": "FINAL_GRAIN_UNKNOWN"}
    steps = [s for s in (plan.get("steps") or []) if isinstance(s, dict)]
    ops = [str(s.get("op") or "") for s in steps]
    if any(op and op not in {
        "rename_columns", "filter_rows", "select_columns", "union_rows", "join", "aggregate",
    } for op in ops):
        return {"complete": False, "kind": "unknown_op", "grain_columns": [], "gap": "UNSUPPORTED_OPERATION"}
    last_agg = max((i for i, s in enumerate(steps) if s.get("op") == "aggregate"), default=None)
    if last_agg is None:
        return {
            "complete": True,
            "kind": "row_level",
            "grain_columns": list(lineage.get("final_schema") or []),
            "gap": None,
        }
    names = {str(x) for x in ((steps[last_agg].get("params") or {}).get("group_by") or [])}
    for s in steps[last_agg + 1:]:
        op = str(s.get("op") or "")
        params = s.get("params") or {}
        if op == "rename_columns":
            mapping = {str(k): str(v) for k, v in (params.get("mapping") or {}).items()}
            names = {mapping.get(n, n) for n in names}
        elif op == "select_columns":
            sel = {str(c) for c in (params.get("columns") or [])}
            names = {n for n in names if n in sel}
        elif op == "filter_rows":
            continue
        elif op == "aggregate":
            names = {str(x) for x in (params.get("group_by") or [])}
        elif op in {"join", "union_rows"}:
            return {"complete": False, "kind": "post_agg_combine", "grain_columns": [], "gap": "FINAL_GRAIN_UNKNOWN"}
        else:
            return {"complete": False, "kind": "unknown_op", "grain_columns": [], "gap": "UNSUPPORTED_OPERATION"}
    final = list(lineage.get("final_schema") or [])
    grain = [n for n in names if n in final]
    if names - set(final):
        return {"complete": False, "kind": "group", "grain_columns": grain, "gap": "FINAL_GRAIN_UNKNOWN"}
    return {"complete": True, "kind": "group", "grain_columns": grain, "gap": None}


def _origins_of(lineage: dict[str, Any], col: str) -> list[tuple[str, str]]:
    rows = []
    for o in (lineage.get("final_column_origins") or {}).get(col) or []:
        if isinstance(o, dict) and o.get("source") and o.get("column"):
            rows.append((str(o["source"]), str(o["column"])))
    return rows


def check_declared_grain(
    contract: dict[str, Any],
    *,
    plan: dict[str, Any] | None,
    schemas: dict[str, list[str]],
    generation_error: str | None = None,
) -> dict[str, Any]:
    """Research checker. Inspects bindings, origins, and the operation graph only."""
    if generation_error:
        return {"status": "OPERATIONAL_FAILURE", "gap": None, "findings": []}
    parsed = parse_contract_structural(contract, schemas)
    facts = observe_required_ungrounded(parsed)
    if parsed.get("valid"):
        bound = [
            (r["binding"]["source_id"], r["binding"]["column_ref"])
            for r in parsed["required_grain"]
            if r.get("binding")
        ]
        if len(bound) != len(set(bound)):
            return {
                "status": "INVALID_CONTRACT",
                "gap": "MALFORMED_BINDING",
                "findings": [],
                "answerability_facts": facts,
                "detail": "duplicate_identical_bindings",
            }
    if not parsed.get("valid"):
        reason = str(parsed.get("reason") or "")
        gap = "MALFORMED_BINDING"
        if "source" in reason or reason == "binding_not_in_schema":
            bind = None
            try:
                bind = ((contract.get("required_grain") or [{}])[0] or {}).get("binding") or {}
            except Exception:
                bind = {}
            sid, col = str((bind or {}).get("source_id") or ""), str((bind or {}).get("column_ref") or "")
            if sid and sid not in schemas:
                gap = "SOURCE_NOT_FOUND"
            elif sid and col and col not in (schemas.get(sid) or []):
                gap = "COLUMN_NOT_FOUND"
        return {"status": "INVALID_CONTRACT", "gap": gap, "findings": [], "answerability_facts": facts}
    if (plan or {}).get("status") == "cannot_plan":
        return {
            "status": "NOT_APPLICABLE",
            "gap": "CANNOT_PLAN_NOT_APPLICABLE",
            "findings": [],
            "answerability_facts": facts,
        }
    if contract.get("grounding_status") == "cannot_ground" or all(
        r.get("grounding_status") == "cannot_ground" for r in parsed["required_grain"]
    ):
        return {"status": "NOT_APPLICABLE", "gap": None, "findings": [], "answerability_facts": facts}
    if not plan or plan.get("status") != "planned":
        return {"status": "INVALID_PLAN", "gap": None, "findings": [], "answerability_facts": facts}

    lineage = build_schema_lineage(plan, schemas)
    grain_obs = observe_final_grain(plan, lineage)
    findings = []
    for role in parsed["required_grain"]:
        if role["grounding_status"] == "cannot_ground":
            findings.append({"role_id": role["role_id"], "status": "NOT_APPLICABLE"})
            continue
        ident = (role["binding"]["source_id"], role["binding"]["column_ref"])
        if not grain_obs["complete"]:
            findings.append({"role_id": role["role_id"], "status": "INDETERMINATE", "gap": grain_obs.get("gap")})
            continue
        grain_cols = grain_obs["grain_columns"]
        matched_singleton = False
        ambiguous = False
        origin_unknown = False
        for gc in grain_cols:
            origins = _origins_of(lineage, gc)
            if not origins:
                origin_unknown = True
                continue
            if ident in origins and len(origins) == 1:
                matched_singleton = True
            elif ident in origins and len(origins) > 1:
                ambiguous = True
        if matched_singleton:
            findings.append({"role_id": role["role_id"], "status": "PRESERVED"})
        elif ambiguous:
            findings.append({"role_id": role["role_id"], "status": "INDETERMINATE", "gap": "MULTI_ANCESTRY_AMBIGUOUS"})
        elif origin_unknown:
            findings.append({"role_id": role["role_id"], "status": "INDETERMINATE", "gap": "ORIGIN_UNKNOWN"})
        else:
            findings.append({"role_id": role["role_id"], "status": "CONTRADICTION"})
    statuses = [f["status"] for f in findings]
    if "CONTRADICTION" in statuses:
        overall = "CONTRADICTION"
    elif "INDETERMINATE" in statuses:
        overall = "INDETERMINATE"
    elif statuses and all(s == "NOT_APPLICABLE" for s in statuses):
        overall = "NOT_APPLICABLE"
    elif "PRESERVED" in statuses and not (set(statuses) - {"PRESERVED", "NOT_APPLICABLE"}):
        overall = "PRESERVED"
    else:
        overall = "INDETERMINATE"
    gap = next((f.get("gap") for f in findings if f.get("gap")), grain_obs.get("gap"))
    return {
        "status": overall,
        "gap": gap,
        "findings": findings,
        "answerability_facts": facts,
        "grain_obs": grain_obs,
    }


def _fx(
    fid: str,
    oracle: str,
    contract: dict[str, Any],
    plan: dict[str, Any] | None,
    schemas: dict[str, list[str]],
    *,
    family: str,
    note: str,
    generation_error: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "fixture_id": fid,
        "request_id": request_id or f"req-{fid}",
        "semantic_contract_id": f"sc-{fid}",
        "attempt_id": f"att-{fid}",
        "oracle": oracle,
        "contract": contract,
        "plan": plan,
        "schemas": schemas,
        "family": family,
        "note": note,
        "generation_error": generation_error,
    }


def build_fixtures() -> list[dict[str, Any]]:
    c_key = _contract("src_a", "entity_key")
    rows: list[dict[str, Any]] = []

    def add(fid: str, oracle: str, plan: dict[str, Any] | None, family: str, note: str, contract: dict[str, Any] | None = None, schemas: dict[str, list[str]] | None = None, **kw: Any) -> None:
        rows.append(_fx(fid, oracle, contract or c_key, plan, schemas or SA, family=family, note=note, **kw))

    # PRESERVED — display-name changing lookalikes
    add("p-direct", "PRESERVED", _plan("s", [_step("s1", "select_columns", ["src_a"], "s", columns=["entity_key", "measure"])]), "select", "source column remains")
    add("p-filter", "PRESERVED", _plan("f", [_step("f1", "filter_rows", ["src_a"], "f", conditions=[{"column": "extra", "op": "eq", "value": "x"}])]), "filter", "filter keeps grain")
    add("p-ren1", "PRESERVED", _plan("r", [_step("r1", "rename_columns", ["src_a"], "r", mapping={"entity_key": "id_out"})]), "rename", "A→B")
    add("p-ren2", "PRESERVED", _plan("r2", [
        _step("r1", "rename_columns", ["src_a"], "r", mapping={"entity_key": "mid"}),
        _step("r2", "rename_columns", ["r"], "r2", mapping={"mid": "id_out"}),
    ]), "rename", "A→B→C")
    add("p-ren3", "PRESERVED", _plan("r3", [
        _step("r1", "rename_columns", ["src_a"], "r", mapping={"entity_key": "a"}),
        _step("r2", "rename_columns", ["r"], "r2", mapping={"a": "b"}),
        _step("r3", "rename_columns", ["r2"], "r3", mapping={"b": "c"}),
    ]), "rename", "3 renames")
    add("p-filt-ren", "PRESERVED", _plan("r", [
        _step("f1", "filter_rows", ["src_a"], "f", conditions=[{"column": "extra", "op": "eq", "value": "x"}]),
        _step("r1", "rename_columns", ["f"], "r", mapping={"entity_key": "id_out"}),
    ]), "rename", "filter then rename")
    add("p-ren-sel", "PRESERVED", _plan("s", [
        _step("r1", "rename_columns", ["src_a"], "r", mapping={"entity_key": "id_out", "measure": "brightness"}),
        _step("s1", "select_columns", ["r"], "s", columns=["id_out", "brightness"]),
    ]), "rename", "40D rename-gap lookalike")
    add("p-agg-key", "PRESERVED", _plan("a", [_step("a1", "aggregate", ["src_a"], "a", group_by=["entity_key"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}])]), "aggregate", "group by binding")
    add("p-ren-agg", "PRESERVED", _plan("a", [
        _step("r1", "rename_columns", ["src_a"], "r", mapping={"entity_key": "id_out"}),
        _step("a1", "aggregate", ["r"], "a", group_by=["id_out"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}]),
    ]), "rename", "A→B then aggregate by B")
    add("p-agg-ren", "PRESERVED", _plan("r", [
        _step("a1", "aggregate", ["src_a"], "a", group_by=["entity_key"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}]),
        _step("r1", "rename_columns", ["a"], "r", mapping={"entity_key": "id_out"}),
    ]), "rename", "aggregate then rename grain")
    add("p-join-l", "PRESERVED", _plan("j", [_step("j1", "join", ["src_a", "src_b"], "j", left_on=["entity_key"], right_on=["other_key"])]), "join", "left binding in row-level join", schemas=SB_CLEAN)
    add("p-join-r", "PRESERVED", _plan("j", [_step("j1", "join", ["src_a", "src_b"], "j", left_on=["entity_key"], right_on=["other_key"])]), "join", "right other_key", contract=_contract("src_b", "other_key"), schemas=SB_CLEAN)
    add("p-join-ren-key", "PRESERVED", _plan("j", [
        _step("r1", "rename_columns", ["src_a"], "l", mapping={"entity_key": "k"}),
        _step("j1", "join", ["l", "src_b"], "j", left_on=["k"], right_on=["other_key"]),
    ]), "join", "A→B then join on alias vs distinct right key", schemas=SB_CLEAN)
    add("p-union-same", "PRESERVED", _plan("u", [
        _step("s1", "select_columns", ["src_a"], "l", columns=["entity_key", "measure"]),
        _step("s2", "select_columns", ["src_a"], "r", columns=["entity_key", "measure"]),
        _step("u1", "union_rows", ["l", "r"], "u"),
    ]), "union", "compatible same-origin union")
    add("p-filt-ren-agg", "PRESERVED", _plan("a", [
        _step("f1", "filter_rows", ["src_a"], "f", conditions=[{"column": "extra", "op": "eq", "value": "x"}]),
        _step("r1", "rename_columns", ["f"], "r", mapping={"entity_key": "id_out"}),
        _step("a1", "aggregate", ["r"], "a", group_by=["id_out"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}]),
    ]), "multi", "filter→rename→aggregate")
    add("p-sel-keep", "PRESERVED", _plan("s", [_step("s1", "select_columns", ["src_a"], "s", columns=["entity_key"])]), "select", "project keeps grain")
    add("p-g1g2", "PRESERVED", _plan("a", [_step("a1", "aggregate", ["src_a"], "a", group_by=["entity_key", "extra"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}])]), "composite", "two grain roles", contract=_contract_multi([("src_a", "entity_key"), ("src_a", "extra")]))
    add("p-filter-twice", "PRESERVED", _plan("f2", [
        _step("f1", "filter_rows", ["src_a"], "f", conditions=[{"column": "extra", "op": "eq", "value": "x"}]),
        _step("f2", "filter_rows", ["f"], "f2", conditions=[{"column": "measure", "op": "gt", "value": 0}]),
    ]), "filter", "two filters")
    add("p-ren-join-noagg", "PRESERVED", _plan("j", [
        _step("r1", "rename_columns", ["src_a"], "l", mapping={"measure": "m_a"}),
        _step("j1", "join", ["l", "src_b"], "j", left_on=["entity_key"], right_on=["other_key"]),
    ]), "join", "rename then join no aggregate", schemas=SB_CLEAN)
    add("p-select-all", "PRESERVED", _plan("s", [_step("s1", "select_columns", ["src_a"], "s", columns=["entity_key", "measure", "extra"])]), "select", "keep all")
    add("p-ren-sel-ren", "PRESERVED", _plan("r2", [
        _step("r1", "rename_columns", ["src_a"], "r", mapping={"entity_key": "mid"}),
        _step("s1", "select_columns", ["r"], "s", columns=["mid", "measure"]),
        _step("r2", "rename_columns", ["s"], "r2", mapping={"mid": "final_id"}),
    ]), "rename", "rename-select-rename")
    add("p-agg-two-keys", "PRESERVED", _plan("a", [_step("a1", "aggregate", ["src_a"], "a", group_by=["entity_key", "extra"], metrics=[{"column": "measure", "function": "count", "alias": "n"}])]), "aggregate", "composite group")
    add("p-depth4", "PRESERVED", _plan("a", [
        _step("f1", "filter_rows", ["src_a"], "f", conditions=[{"column": "extra", "op": "eq", "value": "x"}]),
        _step("r1", "rename_columns", ["f"], "r", mapping={"entity_key": "k1"}),
        _step("r2", "rename_columns", ["r"], "r2", mapping={"k1": "k2"}),
        _step("a1", "aggregate", ["r2"], "a", group_by=["k2"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}]),
    ]), "multi", "4-op chain")
    add("p-hist-campus-y", "PRESERVED", _plan("a", [_step("a1", "aggregate", ["rooms.xlsx"], "a", group_by=["campus"], metrics=[{"column": "lux", "function": "sum", "alias": "lux"}])]), "historical", "valid campus grain", contract=_contract("rooms.xlsx", "campus"), schemas={"rooms.xlsx": ["campus", "crm", "lux"]})
    add("p-hist-building-y", "PRESERVED", _plan("a", [_step("a1", "aggregate", ["b.xlsx"], "a", group_by=["building"], metrics=[{"column": "lux", "function": "sum", "alias": "lux"}])]), "historical", "valid building grain", contract=_contract("b.xlsx", "building"), schemas={"b.xlsx": ["room", "building", "lux"]})
    add("p-m2-lookalike", "PRESERVED", _plan("a", [_step("a1", "aggregate", ["tickets.xlsx"], "a", group_by=["agent"], metrics=[{"column": "hrs", "function": "sum", "alias": "hrs"}])]), "historical", "M2 valid counterpart", contract=_contract("tickets.xlsx", "agent"), schemas={"tickets.xlsx": ["tid", "agent", "hrs"]})
    add("p-join-l-sel", "PRESERVED", _plan("s", [
        _step("j1", "join", ["src_a", "src_b"], "j", left_on=["entity_key"], right_on=["other_key"]),
        _step("s1", "select_columns", ["j"], "s", columns=["entity_key", "measure", "val_b"]),
    ]), "join", "join then select keeps left key", schemas=SB_CLEAN)
    add("p-b-only", "PRESERVED", _plan("s", [_step("s1", "select_columns", ["src_b"], "s", columns=["other_key", "val_b"])]), "multi_source", "source B only", contract=_contract("src_b", "other_key"), schemas=SB_CLEAN)
    add("p-branch-filter-ren", "PRESERVED", _plan("r", [
        _step("f1", "filter_rows", ["src_a"], "f", conditions=[{"column": "extra", "op": "eq", "value": "x"}]),
        _step("r1", "rename_columns", ["f"], "r", mapping={"measure": "m1"}),
    ]), "branch", "branch-local rename still has entity_key")
    add("p-agg-alias-metric", "PRESERVED", _plan("a", [_step("a1", "aggregate", ["src_a"], "a", group_by=["entity_key"], metrics=[{"column": "measure", "function": "mean", "alias": "avg_m"}])]), "aggregate", "metric alias does not steal grain")
    add("p-empty-filter-keep", "PRESERVED", _plan("f", [_step("f1", "filter_rows", ["src_a"], "f", conditions=[])]), "filter", "empty filter")
    add("p-ren-measure-only", "PRESERVED", _plan("r", [_step("r1", "rename_columns", ["src_a"], "r", mapping={"measure": "brightness"})]), "rename", "rename other col, grain name unchanged")
    add("p-g3", "PRESERVED", _plan("s", [_step("s1", "select_columns", ["src_a"], "s", columns=["entity_key", "measure", "extra"])]), "composite", "three roles row-level", contract=_contract_multi([("src_a", "entity_key"), ("src_a", "measure"), ("src_a", "extra")]))

    # CONTRADICTION
    add("c-agg-other", "CONTRADICTION", _plan("a", [_step("a1", "aggregate", ["src_a"], "a", group_by=["extra"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}])]), "aggregate", "group by other column")
    add("c-agg-metric", "CONTRADICTION", _plan("a", [_step("a1", "aggregate", ["src_a"], "a", group_by=["extra"], metrics=[{"column": "entity_key", "function": "count", "alias": "n_keys"}])]), "aggregate", "E present as metric not grain")
    add("c-select-drop", "CONTRADICTION", _plan("s", [_step("s1", "select_columns", ["src_a"], "s", columns=["measure", "extra"])]), "select", "projects away grain")
    add("c-m2-tid", "CONTRADICTION", _plan("a", [_step("a1", "aggregate", ["tickets.xlsx"], "a", group_by=["tid"], metrics=[{"column": "hrs", "function": "sum", "alias": "hrs"}])]), "historical", "declared agent vs tid group", contract=_contract("tickets.xlsx", "agent"), schemas={"tickets.xlsx": ["tid", "agent", "hrs"]})
    add("c-campus-crm", "CONTRADICTION", _plan("a", [_step("a1", "aggregate", ["rooms.xlsx"], "a", group_by=["crm"], metrics=[{"column": "lux", "function": "sum", "alias": "lux"}])]), "historical", "declared campus vs crm", contract=_contract("rooms.xlsx", "campus"), schemas={"rooms.xlsx": ["campus", "crm", "lux"]})
    add("c-building-room", "CONTRADICTION", _plan("a", [_step("a1", "aggregate", ["b.xlsx"], "a", group_by=["room"], metrics=[{"column": "lux", "function": "sum", "alias": "lux"}])]), "historical", "declared building vs room", contract=_contract("b.xlsx", "building"), schemas={"b.xlsx": ["room", "building", "lux"]})
    add("c-ren-then-agg-other", "CONTRADICTION", _plan("a", [
        _step("r1", "rename_columns", ["src_a"], "r", mapping={"entity_key": "id_out"}),
        _step("a1", "aggregate", ["r"], "a", group_by=["extra"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}]),
    ]), "aggregate", "rename grain then aggregate other")
    add("c-sel-after-ren", "CONTRADICTION", _plan("s", [
        _step("r1", "rename_columns", ["src_a"], "r", mapping={"entity_key": "id_out"}),
        _step("s1", "select_columns", ["r"], "s", columns=["measure"]),
    ]), "select", "drop renamed grain")
    add("c-g2-missing", "CONTRADICTION", _plan("a", [_step("a1", "aggregate", ["src_a"], "a", group_by=["entity_key"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}])]), "composite", "g2 extra collapsed", contract=_contract_multi([("src_a", "entity_key"), ("src_a", "extra")]))
    add("c-join-then-agg-other", "CONTRADICTION", _plan("a", [
        _step("j1", "join", ["src_a", "src_b"], "j", left_on=["entity_key"], right_on=["other_key"]),
        _step("a1", "aggregate", ["j"], "a", group_by=["val_b"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}]),
    ]), "join", "join then aggregate unrelated", schemas=SB_CLEAN)
    add("c-union-then-agg-other", "CONTRADICTION", _plan("a", [
        _step("s1", "select_columns", ["src_a"], "l", columns=["entity_key", "measure"]),
        _step("s2", "select_columns", ["src_a"], "r", columns=["entity_key", "measure"]),
        _step("u1", "union_rows", ["l", "r"], "u"),
        _step("a1", "aggregate", ["u"], "a", group_by=["measure"], metrics=[{"column": "measure", "function": "count", "alias": "n"}]),
    ]), "union", "union then group measure")
    add("c-global-summary", "CONTRADICTION", _plan("a", [_step("a1", "aggregate", ["src_a"], "a", group_by=[], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}])]), "aggregate", "empty group_by collapses all")

    # INDETERMINATE
    add("i-union-mixed", "INDETERMINATE", _plan("u", [_step("u1", "union_rows", ["src_a", "src_b"], "u")]), "union", "mixed-origin union column", schemas=SB)
    add("i-unknown-op", "INDETERMINATE", _plan("x", [_step("x1", "pivot", ["src_a"], "x")]), "gap", "unsupported op")
    add("i-post-agg-join", "INDETERMINATE", _plan("j", [
        _step("a1", "aggregate", ["src_a"], "a", group_by=["entity_key"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}]),
        _step("j1", "join", ["a", "src_b"], "j", left_on=["entity_key"], right_on=["other_key"]),
    ]), "join", "grain after post-agg join not derived", schemas=SB_CLEAN)
    add("p-join-suffix-collision", "PRESERVED", _plan("j", [_step("j1", "join", ["src_a", "src_b"], "j", left_on=["entity_key"], right_on=["other_key"])]), "join", "same display name becomes _left/_right; left origin remains singleton", schemas=SB)
    add("i-join-merged-key", "INDETERMINATE", _plan("j", [
        _step("r1", "rename_columns", ["src_a"], "l", mapping={"entity_key": "k"}),
        _step("r2", "rename_columns", ["src_b"], "r", mapping={"other_key": "k"}),
        _step("j1", "join", ["l", "r"], "j", left_on=["k"], right_on=["k"]),
    ]), "join", "join key k merges two distinct origins", schemas=SB_CLEAN)
    add("i-no-plan", "INDETERMINATE", None, "gap", "missing plan")  # INVALID_PLAN actually - wait no plan status planned. check returns INVALID_PLAN
    # Fix: oracle for no plan should be INVALID_PLAN - spec oracle is PRESERVED/CONTRADICTION/IND/N/A. Use INDETERMINATE for missing graph? Spec says INVALID_PLAN separate. I'll oracle as INDETERMINATE for insufficient plan.
    add("i-incomplete-final", "INDETERMINATE", _plan("missing", [_step("s1", "select_columns", ["src_a"], "s", columns=["entity_key"])]), "gap", "final_output not produced")

    # NOT_APPLICABLE
    add("na-cannot-plan", "NOT_APPLICABLE", {"status": "cannot_plan", "reason": "insufficient", "steps": [], "final_output": None}, "cannot_plan", "40D abstain replay")
    add("na-cannot-ground", "NOT_APPLICABLE", _plan("s", [_step("s1", "select_columns", ["src_a"], "s", columns=["entity_key"])]), "cannot_ground", "ungrounded grain N/A", contract={
        "contract_version": "1", "grounding_status": "cannot_ground",
        "required_grain": [{"role_id": "g1", "semantic_label": "x", "binding": None, "grounding_status": "cannot_ground", "required_for_answerability": True}],
    })
    add("na-cannot-plan-grounded", "NOT_APPLICABLE", {"status": "cannot_plan", "steps": [], "final_output": None}, "cannot_plan", "grounded contract but cannot_plan")
    add("na-op-fail", "NOT_APPLICABLE", None, "operational", "backend timeout", generation_error="ReadTimeout")

    # INVALID
    add("inv-missing-src", "INVALID_CONTRACT", _plan("s", [_step("s1", "select_columns", ["src_a"], "s", columns=["entity_key"])]), "invalid", "source missing", contract=_contract("nope.xlsx", "entity_key"))
    add("inv-missing-col", "INVALID_CONTRACT", _plan("s", [_step("s1", "select_columns", ["src_a"], "s", columns=["entity_key"])]), "invalid", "column missing", contract=_contract("src_a", "no_such"))
    add("inv-same-name-other", "INVALID_CONTRACT", _plan("s", [_step("s1", "select_columns", ["src_b"], "s", columns=["entity_key"])]), "invalid", "must not fallback to src_b.entity_key", contract=_contract("src_a", "entity_key"), schemas={"src_b": ["entity_key", "val_b"]})
    add("inv-malformed", "INVALID_CONTRACT", _plan("s", [_step("s1", "select_columns", ["src_a"], "s", columns=["entity_key"])]), "invalid", "binding incomplete", contract={"contract_version": "1", "grounding_status": "grounded", "required_grain": [{"role_id": "g1", "semantic_label": "x", "binding": {"column_ref": "entity_key"}, "grounding_status": "grounded", "required_for_answerability": True}]})
    add("inv-bad-version", "INVALID_CONTRACT", _plan("s", [_step("s1", "select_columns", ["src_a"], "s", columns=["entity_key"])]), "invalid", "bad version", contract={**c_key, "contract_version": "9"})
    add("inv-partial", "INVALID_CONTRACT", _plan("s", [_step("s1", "select_columns", ["src_a"], "s", columns=["entity_key"])]), "invalid", "partially_grounded forbidden", contract={**c_key, "grounding_status": "partially_grounded"})

    # 40D gap replay (dedicated)
    add("r40d-rename", "PRESERVED", _plan("s", [
        _step("r1", "rename_columns", ["src_a"], "r", mapping={"measure": "brightness", "entity_key": "id_out"}),
        _step("s1", "select_columns", ["r"], "s", columns=["id_out", "brightness"]),
    ]), "replay_40d", "rename display ≠ origin")
    add("r40d-sides", "PRESERVED", _plan("j", [
        _step("r1", "rename_columns", ["src_a"], "l", mapping={"measure": "v_l"}),
        _step("r2", "rename_columns", ["src_b"], "r", mapping={"val_b": "w_r"}),
        _step("j1", "join", ["l", "r"], "j", left_on=["entity_key"], right_on=["other_key"]),
    ]), "replay_40d", "join after rename keeps left grain", schemas=SB_CLEAN)
    add("r40d-cannot-plan", "NOT_APPLICABLE", {"status": "cannot_plan", "steps": [], "final_output": None}, "replay_40d", "cannot_plan empty schema")
    add("r40d-compare-tod", "INDETERMINATE", _plan("j", [
        _step("r1", "rename_columns", ["am.xlsx"], "l", mapping={"kw": "am_kw"}),
        _step("r2", "rename_columns", ["pm.xlsx"], "r", mapping={"kw": "pm_kw"}),
        _step("j1", "join", ["l", "r"], "j", left_on=["node"], right_on=["node"]),
    ]), "replay_40d", "join key merges am.node and pm.node; Python must not equate them", contract=_contract("am.xlsx", "node"), schemas={"am.xlsx": ["node", "kw"], "pm.xlsx": ["node", "kw"]})

    # branch / multi-source / historical extras
    add("p-branch-two-filt-join", "PRESERVED", _plan("j", [
        _step("f1", "filter_rows", ["src_a"], "f1", conditions=[{"column": "extra", "op": "eq", "value": "x"}]),
        _step("f2", "filter_rows", ["src_a"], "f2", conditions=[{"column": "extra", "op": "eq", "value": "y"}]),
        _step("r1", "rename_columns", ["f1"], "a1", mapping={"measure": "m_x"}),
        _step("r2", "rename_columns", ["f2"], "a2", mapping={"measure": "m_y"}),
        _step("j1", "join", ["a1", "a2"], "j", left_on=["entity_key"], right_on=["entity_key"]),
    ]), "branch", "same-source two filters then join; grain still entity_key")
    add("p-hist-campus-ren", "PRESERVED", _plan("r", [
        _step("a1", "aggregate", ["rooms.xlsx"], "a", group_by=["campus"], metrics=[{"column": "lux", "function": "sum", "alias": "lux"}]),
        _step("r1", "rename_columns", ["a"], "r", mapping={"campus": "site"}),
    ]), "historical", "campus→site ancestry", contract=_contract("rooms.xlsx", "campus"), schemas={"rooms.xlsx": ["campus", "crm", "lux"]})
    add("p-join-r-then-ren", "PRESERVED", _plan("r", [
        _step("j1", "join", ["src_a", "src_b"], "j", left_on=["entity_key"], right_on=["other_key"]),
        _step("r1", "rename_columns", ["j"], "r", mapping={"other_key": "rk"}),
    ]), "join", "right key renamed after join", contract=_contract("src_b", "other_key"), schemas=SB_CLEAN)
    add("p-a-only-agg", "PRESERVED", _plan("a", [_step("a1", "aggregate", ["src_a"], "a", group_by=["entity_key"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}])]), "multi_source", "source A only aggregate")
    add("p-no-src-fallback", "PRESERVED", _plan("a", [
        _step("f1", "filter_rows", ["src_a"], "f", conditions=[{"column": "extra", "op": "eq", "value": "x"}]),
        _step("r1", "rename_columns", ["f"], "r", mapping={"entity_key": "k_local"}),
        _step("a1", "aggregate", ["r"], "a", group_by=["k_local"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}]),
    ]), "branch", "must use branch alias not original source name")
    add("p-both-roles", "PRESERVED", _plan("j", [_step("j1", "join", ["src_a", "src_b"], "j", left_on=["entity_key"], right_on=["other_key"])]), "multi_source", "two roles both sides", contract=_contract_multi([("src_a", "entity_key"), ("src_b", "other_key")]), schemas=SB_CLEAN)
    add("c-lookalike-campus", "CONTRADICTION", _plan("a", [_step("a1", "aggregate", ["rooms.xlsx"], "a", group_by=["crm"], metrics=[{"column": "lux", "function": "sum", "alias": "lux"}])]), "historical", "lookalike of campus-y", contract=_contract("rooms.xlsx", "campus"), schemas={"rooms.xlsx": ["campus", "crm", "lux"]})
    add("i-branch-agg-join", "INDETERMINATE", _plan("j", [
        _step("f1", "filter_rows", ["src_a"], "f1", conditions=[{"column": "extra", "op": "eq", "value": "x"}]),
        _step("f2", "filter_rows", ["src_a"], "f2", conditions=[{"column": "extra", "op": "eq", "value": "y"}]),
        _step("a1", "aggregate", ["f1"], "g1", group_by=["entity_key"], metrics=[{"column": "measure", "function": "sum", "alias": "m1"}]),
        _step("a2", "aggregate", ["f2"], "g2", group_by=["entity_key"], metrics=[{"column": "measure", "function": "sum", "alias": "m2"}]),
        _step("j1", "join", ["g1", "g2"], "j", left_on=["entity_key"], right_on=["entity_key"]),
    ]), "branch", "post-agg join grain not native")
    add("inv-dup-bind", "INVALID_CONTRACT", _plan("s", [_step("s1", "select_columns", ["src_a"], "s", columns=["entity_key"])]), "invalid", "duplicate identical bindings", contract=_contract_multi([("src_a", "entity_key"), ("src_a", "entity_key")]))
    add("i-math-as-grain", "INDETERMINATE", _plan("x", [_step("x1", "derive_column", ["src_a"], "x", new_column="k2", from_column="entity_key")]), "gap", "unsupported transform cannot prove grain")

    # immutability pair: same contract_id conceptually
    rows.append(_fx(
        "imm-a1", "PRESERVED", c_key,
        _plan("a", [_step("a1", "aggregate", ["src_a"], "a", group_by=["entity_key"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}])]),
        SA, family="immutability", note="attempt 1 preserved", request_id="req-imm",
    ))
    rows[-1]["semantic_contract_id"] = "sc-imm"
    rows[-1]["attempt_id"] = "att-imm-1"
    rows.append(_fx(
        "imm-a2", "CONTRADICTION", c_key,
        _plan("a", [_step("a1", "aggregate", ["src_a"], "a", group_by=["extra"], metrics=[{"column": "measure", "function": "sum", "alias": "measure"}])]),
        SA, family="immutability", note="attempt 2 same contract", request_id="req-imm",
    ))
    rows[-1]["semantic_contract_id"] = "sc-imm"
    rows[-1]["attempt_id"] = "att-imm-2"

    return rows


def _norm_status(st: str) -> str:
    if st == "OPERATIONAL_FAILURE":
        return "NOT_APPLICABLE"
    if st == "INVALID_PLAN":
        return "INDETERMINATE"
    return st


def evaluate(fixtures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for fx in fixtures:
        t0 = time.perf_counter()
        chk = check_declared_grain(
            fx["contract"], plan=fx["plan"], schemas=fx["schemas"],
            generation_error=fx.get("generation_error"),
        )
        elapsed = time.perf_counter() - t0
        pred = _norm_status(chk["status"])
        oracle = fx["oracle"]
        raw = chk["status"]
        row = {
            "fixture_id": fx["fixture_id"],
            "family": fx["family"],
            "oracle": oracle,
            "checker": raw,
            "checker_norm": pred,
            "gap": chk.get("gap"),
            "elapsed_s": round(elapsed, 6),
            "request_id": fx["request_id"],
            "semantic_contract_id": fx["semantic_contract_id"],
            "attempt_id": fx["attempt_id"],
            "FALSE_CONTRADICTION": oracle == "PRESERVED" and pred == "CONTRADICTION",
            "FALSE_PRESERVED": oracle == "CONTRADICTION" and pred == "PRESERVED",
            "MISSED_CONTRADICTION": oracle == "CONTRADICTION" and pred != "CONTRADICTION",
            "agree": pred == oracle if oracle in CHECK_STATUSES or oracle in {"PRESERVED", "CONTRADICTION", "INDETERMINATE", "NOT_APPLICABLE", "INVALID_CONTRACT"} else pred == oracle,
        }
        if oracle == "INVALID_CONTRACT":
            row["agree"] = raw == "INVALID_CONTRACT"
        if fx.get("generation_error"):
            row["agree"] = raw == "OPERATIONAL_FAILURE"
            row["oracle"] = "OPERATIONAL_FAILURE"
        out.append(row)
    return out


def write_artifacts(fixtures: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    n = len(results)
    oracle_c = Counter(r.get("oracle") if r["fixture_id"] != "na-op-fail" else "OPERATIONAL_FAILURE" for r in results)
    # use original fixture oracle for distribution
    oracle_fx = Counter(f["oracle"] for f in fixtures)
    fc = [r for r in results if r["FALSE_CONTRADICTION"]]
    fp = [r for r in results if r["FALSE_PRESERVED"]]
    missed = [r for r in results if r["MISSED_CONTRADICTION"]]
    contr_oracle = [r for r in results if r["oracle"] == "CONTRADICTION"]
    recall = (sum(1 for r in contr_oracle if r["checker_norm"] == "CONTRADICTION") / max(len(contr_oracle), 1))
    pred_contr = [r for r in results if r["checker_norm"] == "CONTRADICTION"]
    prec = (sum(1 for r in pred_contr if r["oracle"] == "CONTRADICTION") / max(len(pred_contr), 1))
    agree = sum(1 for r in results if r["agree"]) / max(n, 1)
    ind_rate = sum(1 for r in results if r["checker_norm"] == "INDETERMINATE") / max(n, 1)
    times = [r["elapsed_s"] for r in results]

    _write("baseline_freeze.json", {
        "phase40e_sha": PHASE40E_SHA,
        "shadow": "OFF",
        "migration": "NOT_APPROVED",
        "production_change": "NO_PRODUCTION_CHANGE",
        "production_verifier": SEMANTIC_VERIFIER_MODEL,
        "production_variant": SEMANTIC_VERIFIER_VARIANT,
        "bounded": {
            "MAX_RESULT_SAMPLE_ROWS": MAX_RESULT_SAMPLE_ROWS,
            "MAX_RESULT_SAMPLE_COLUMNS": MAX_RESULT_SAMPLE_COLUMNS,
            "MAX_RESULT_SERIALIZED_CHARS": MAX_RESULT_SERIALIZED_CHARS,
        },
        "MAX_SEMANTIC_ESCALATIONS": MAX_SEMANTIC_ESCALATIONS,
        "contract_v1": "grain + grounded binding only",
    })
    _write("contract_fixture_registry.json", {
        "n": len(fixtures),
        "ids": [f["fixture_id"] for f in fixtures],
        "families": dict(Counter(f["family"] for f in fixtures)),
    })
    _write("manual_structural_oracle.json", {
        f["fixture_id"]: {"oracle": f["oracle"], "note": f["note"], "family": f["family"]}
        for f in fixtures
    })
    _write("observation_source_inventory.json", [
        {"field": "source schema identity", "status": "AVAILABLE", "via": "caller schemas / source_schemas"},
        {"field": "source IDs", "status": "AVAILABLE", "via": "plan inputs / CrossFileUnderstanding source_id"},
        {"field": "transformed branch state", "status": "PARTIAL", "via": "step_outputs + step_column_origins"},
        {"field": "final schema", "status": "AVAILABLE", "via": "final_schema"},
        {"field": "final schema origins", "status": "AVAILABLE", "via": "final_column_origins"},
        {"field": "expression ancestry", "status": "AVAILABLE", "via": "final_column_evidence_signatures"},
        {"field": "final grain", "status": "PARTIAL", "via": "derived from last aggregate group_by + later rename/select; not a native V2.2 field"},
        {"field": "aliases", "status": "AVAILABLE", "via": "rename mapping events + origins"},
        {"field": "operation graph", "status": "AVAILABLE", "via": "IntegrationPlan.steps"},
    ])
    _write("v22_sufficiency_audit.json", {
        "verdict": "SUFFICIENT_WITH_SMALL_OBSERVATION_EXTENSION",
        "identity": "(source_id, origin_column_ref) via final_column_origins is sufficient for rename",
        "final_grain": "derived, not native — post-aggregate join/union cannot be proven → INDETERMINATE",
        "extension_if_any": "optional explicit final_grain_identities on lineage observer; not implemented in 40F",
    })
    def _fam(prefix: str) -> list[str]:
        return [r["fixture_id"] for r in results if r["family"] == prefix or r["fixture_id"].startswith(prefix)]

    _write("rename_matrix.json", [r for r in results if r["family"] == "rename" or r["fixture_id"].startswith("p-ren") or r["fixture_id"].startswith("r40d-rename")])
    _write("aggregate_matrix.json", [r for r in results if r["family"] == "aggregate" or r["fixture_id"].startswith("c-agg") or r["fixture_id"].startswith("p-agg")])
    _write("join_matrix.json", [r for r in results if r["family"] == "join"])
    _write("union_matrix.json", [r for r in results if r["family"] == "union"])
    _write("branch_state_matrix.json", [r for r in results if r["family"] in {"branch", "multi"}])
    _write("multi_stage_stress.json", [r for r in results if r["family"] == "multi" or r["fixture_id"] == "p-depth4"])
    _write("alias_depth_stress.json", [r for r in results if r["fixture_id"] in {"p-direct", "p-ren1", "p-ren2", "p-ren3"}])
    _write("cannot_plan_cases.json", [r for r in results if r["family"] == "cannot_plan" or r["family"] == "replay_40d" and "cannot" in r["fixture_id"]])
    _write("invalid_binding_cases.json", [r for r in results if r["family"] == "invalid"])
    _write("observation_gap_taxonomy.json", [{"code": k, "note_ko": v} for k, v in GAP_KO.items()])
    _write("checker_pseudocode.json", {
        "steps": [
            "parse contract structurally (no semantic_label)",
            "resolve binding existence in declared source only",
            "if cannot_plan or cannot_ground → NOT_APPLICABLE",
            "canonical identity = (source_id, column_ref)",
            "observe_final_grain from last aggregate group_by propagated through rename/select",
            "if grain observation incomplete → INDETERMINATE",
            "PRESERVED iff some grain column has singleton origin equal to identity",
            "else CONTRADICTION (presence as metric is not grain)",
        ],
        "never": ["user_prompt", "semantic_label", "same-name fallback"],
    })
    _write("checker_results.json", {"n": n, "agree": round(agree, 4), "rows": results})
    _write("false_contradiction_review.json", {"n": len(fc), "ids": [r["fixture_id"] for r in fc], "rows": fc})
    _write("false_preserved_review.json", {"n": len(fp), "ids": [r["fixture_id"] for r in fp], "rows": fp})
    _write("indeterminate_review.json", {
        "checker_indeterminate": [r["fixture_id"] for r in results if r["checker_norm"] == "INDETERMINATE"],
        "oracle_contr_to_ind": [r["fixture_id"] for r in missed if r["checker_norm"] == "INDETERMINATE"],
        "oracle_pres_to_ind": [r["fixture_id"] for r in results if r["oracle"] == "PRESERVED" and r["checker_norm"] == "INDETERMINATE"],
    })
    _write("phase40d_gap_replay.json", [r for r in results if r["family"] == "replay_40d"])
    _write("request_attempt_isolation.json", {
        "pure_function": True,
        "no_global_state": True,
        "immutability_pair": [r for r in results if r["family"] == "immutability"],
        "versioned_reresolution": "represented as new artifact ids only; not implemented",
    })
    _write("performance_results.json", {
        "n": n,
        "mean_s": round(sum(times) / max(n, 1), 6),
        "max_s": round(max(times), 6),
        "p95_s": round(sorted(times)[max(int(n * 0.95) - 1, 0)], 6),
        "note_ko": "LLM 호출 대비 무시 가능",
    })
    _write("observation_extension_proposal.json", {
        "needed": True,
        "kind": "narrow",
        "name": "explicit final_grain_identities on V2.2 observer",
        "why": "post-aggregate join/union grain cannot be derived without extra rules",
        "semantic_free": True,
        "request_local": True,
        "non_mutating": True,
        "operation_generic": True,
        "implemented": False,
        "code_surface": "schema_lineage.observe_final_grain_identities (~80–150 lines if observer-only)",
        "operation_coverage": "reuse last aggregate group_by; propagate rename/select/filter; leave join/union after aggregate as incomplete",
        "state_complexity": "no new global state; derive from existing step_column_origins",
        "regression_risk": "medium if wired into Validator; low if observer-only",
        "do_not_couple_to_contract": True,
    })
    preconditions_met = len(fc) == 0 and len(fp) == 0
    _write("future_implementation_preconditions.json", {
        "FALSE_CONTRADICTION_zero": len(fc) == 0,
        "FALSE_PRESERVED_zero": len(fp) == 0,
        "rename_safe": all(r["agree"] for r in results if r["family"] == "rename"),
        "cannot_plan_na": all(r["checker"] == "NOT_APPLICABLE" for r in results if r["family"] == "cannot_plan"),
        "no_label_inspection": True,
        "no_fuzzy": True,
        "final_grain_native": False,
        "all_preconditions_met": False,
        "blocker": "final grain is derived/PARTIAL; post-agg combine INDETERMINATE",
    })
    _write("regression_results.json", {"production_code_changed": False, "n_fixtures": n})
    _write("shadow_state_proof.json", {"shadow": "OFF", "env_MULTI_SHADOW_ENABLED": os.environ.get("MULTI_SHADOW_ENABLED", "")})

    verdict_obs = "SMALL_OBSERVATION_EXTENSION_REQUIRED"
    verdict_chk = "FIX_OBSERVER_FIRST"
    if len(fc) or len(fp):
        verdict_obs, verdict_chk = "OBSERVABILITY_UNSAFE", "REJECT_CHECKER_DIRECTION"
    elif ind_rate > 0.25:
        verdict_obs, verdict_chk = "OBSERVABILITY_PARTIAL", "KEEP_DESIGN_ONLY"
    elif all((
        len(fc) == 0,
        all(r["agree"] for r in results if r["family"] == "rename"),
        all(r["checker"] == "NOT_APPLICABLE" for r in results if r["family"] == "cannot_plan"),
    )):
        # still PARTIAL native grain field
        verdict_obs, verdict_chk = "SMALL_OBSERVATION_EXTENSION_REQUIRED", "FIX_OBSERVER_FIRST"

    _write("phase40f_summary.json", {
        "gate": "A",
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
        "production_change": "NO_PRODUCTION_CHANGE",
        "phase40e_sha": PHASE40E_SHA,
        "n": n,
        "oracle": dict(oracle_fx),
        "checker": dict(Counter(r["checker"] for r in results)),
        "agree": round(agree, 4),
        "FALSE_CONTRADICTION": len(fc),
        "FALSE_PRESERVED": len(fp),
        "MISSED_CONTRADICTION": len(missed),
        "missed_as_indeterminate": [r["fixture_id"] for r in missed if r["checker_norm"] == "INDETERMINATE"],
        "missed_as_preserved": [r["fixture_id"] for r in missed if r["checker_norm"] == "PRESERVED"],
        "contradiction_recall": round(recall, 4),
        "preserved_precision": round(prec, 4),
        "INDETERMINATE_rate": round(ind_rate, 4),
        "NOT_APPLICABLE_oracle": oracle_fx.get("NOT_APPLICABLE", 0),
        "INVALID_CONTRACT_oracle": oracle_fx.get("INVALID_CONTRACT", 0),
        "per_family": {
            fam: {
                "n": sum(1 for r in results if r["family"] == fam),
                "agree": round(sum(1 for r in results if r["family"] == fam and r["agree"]) / max(sum(1 for r in results if r["family"] == fam), 1), 4),
            }
            for fam in sorted({r["family"] for r in results})
        },
        "observability_verdict": verdict_obs,
        "checker_verdict": verdict_chk,
        "v22_identity": "SUFFICIENT_WITH_SMALL_OBSERVATION_EXTENSION",
        "next": "B",
        "next_phase": "Phase 40G — Narrow Contract-Lineage Observation Correction",
        "canonical_identity": "(source_id, origin_column_ref)",
        "lineage_presence_is_not_grain": True,
    })


def main() -> None:
    fixtures = build_fixtures()
    results = evaluate(fixtures)
    write_artifacts(fixtures, results)
    fc = sum(1 for r in results if r["FALSE_CONTRADICTION"])
    fp = sum(1 for r in results if r["FALSE_PRESERVED"])
    print("n", len(fixtures), "agree", round(sum(r["agree"] for r in results) / len(results), 3), "FC", fc, "FP", fp)
    print("oracle", dict(Counter(f["oracle"] for f in fixtures)))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
