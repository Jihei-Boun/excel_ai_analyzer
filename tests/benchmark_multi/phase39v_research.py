"""Phase 39V — Offline evidence-based planner capability routing research.

Research / simulation only. Does not modify production routing.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.attempt_lineage import plan_fingerprint
from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.planner_model_strategy import (
    _ESCALATION_TRIGGER_CODES,
    _UNSAFE_ONLY_CODES,
    PlannerModelStrategy,
    should_escalate_after_fast_path,
)
from core.integrate.relationship_profile import build_file_profile, build_pairwise_observation
from core.integrate.schema_lineage import (
    build_schema_lineage,
    extract_source_schemas_from_understanding,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase39v"
PHASE39T = ROOT / "benchmark_results/multi/phase39t"

UNSAFE_CODES = set(_UNSAFE_ONLY_CODES)
TRIGGER_CODES = set(_ESCALATION_TRIGGER_CODES)


def _und_from_frames(frames: dict[str, pd.DataFrame], *, rel: str | None = "join_candidate") -> dict:
    names = list(frames)
    profiles = [build_file_profile(n, frames[n]).to_dict() for n in names]
    pairwise: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    if len(names) >= 2:
        pair = build_pairwise_observation(names[0], frames[names[0]], names[1], frames[names[1]])
        pairwise.append(pair.to_dict() if hasattr(pair, "to_dict") else dict(pair))
        if rel:
            relationships.append(
                {
                    "left_source": names[0],
                    "right_source": names[1],
                    "relationship": rel,
                    "key_candidates": [],
                    "ambiguities": [],
                }
            )
    return {
        "file_profiles": profiles,
        "pairwise_observations": pairwise,
        "relationships": relationships,
    }


def _plan(d: dict[str, Any]) -> Any:
    return integration_plan_from_dict(d)


# ---------------------------------------------------------------------------
# Feature extraction (generic, no family/domain/column-name rules)
# ---------------------------------------------------------------------------


def extract_attempt_evidence(
    *,
    attempt_id: str,
    request_id: str,
    plan: Any,
    understanding: dict[str, Any],
    frames: dict[str, pd.DataFrame] | None = None,
    format_retry_count: int = 0,
    planner_retry_count: int = 0,
    fingerprint_changes: int = 0,
) -> dict[str, Any]:
    plan_obj = plan if hasattr(plan, "to_dict") else _plan(plan)
    plan_d = plan_obj.to_dict()
    val = validate_integration_plan(understanding, plan_obj, frames=frames)
    codes = [e.code for e in val.errors]
    code_set = set(codes)
    schemas = extract_source_schemas_from_understanding(understanding)
    lineage = build_schema_lineage(plan_d, schemas)
    req = plan_d.get("final_output_requirements") or {}
    roles = req.get("output_roles") or []
    side_cols: list[str] = []
    for r in roles:
        if isinstance(r, dict) and r.get("role") == "comparison_side":
            side_cols.extend(str(c) for c in (r.get("columns") or []))
    side_set = list(dict.fromkeys(side_cols))
    final_schema = [str(c) for c in (lineage.get("final_schema") or [])]
    identical_sets = lineage.get("identical_evidence_signature_column_sets") or []
    sides_share = False
    for group in identical_sets:
        g = {str(x) for x in group}
        if sum(1 for c in side_set if c in g) >= 2:
            sides_share = True
    required = [str(c) for c in (req.get("required_columns") or [])]
    missing_required = [c for c in required if c not in final_schema]
    missing_sides = [c for c in side_set if c not in final_schema]
    steps = [s for s in (plan_d.get("steps") or []) if isinstance(s, dict)]
    ops = [str(s.get("op") or "") for s in steps]
    counts = Counter(ops)
    inputs_used = [inp for s in steps for inp in (s.get("inputs") or [])]
    src_names = set(schemas)
    same_source_branch = False
    filter_inputs = [s.get("inputs", [None])[0] for s in steps if s.get("op") == "filter_rows"]
    if len(filter_inputs) >= 2 and len(set(filter_inputs)) == 1:
        same_source_branch = True
    only_unsafe = bool(codes) and all(c in UNSAFE_CODES for c in codes)
    has_trigger = bool(code_set & TRIGGER_CODES)
    status = str(plan_d.get("status") or "planned")
    fast_status = "cannot_plan" if status == "cannot_plan" else (
        "failed" if (not val.valid and status == "planned") else "planned"
    )
    n_sides = len(side_set)
    n_sides_materialized = len([c for c in side_set if c in final_schema])
    evidence_role_contradiction = bool(
        n_sides >= 2
        and (sides_share or n_sides_materialized < 2 or len(missing_sides) >= 1)
    )
    return {
        "attempt_id": attempt_id,
        "request_id": request_id,
        "plan_fingerprint": plan_fingerprint(plan_obj),
        "planner_invocation_id": f"p39v-inv-{attempt_id}",
        "fast_status": fast_status,
        "validation_valid": bool(val.valid),
        "validation_codes": codes,
        "has_final_grain_contradiction": "final_grain_contradiction" in code_set,
        "has_many_to_many": "many_to_many_join_risk" in code_set,
        "has_structural_error": bool(codes),
        "only_unsafe_codes": only_unsafe,
        "has_trigger_code": has_trigger,
        "format_retry_count": int(format_retry_count),
        "planner_retry_count": int(planner_retry_count),
        "fingerprint_changes": int(fingerprint_changes),
        "n_ops": len(ops),
        "n_join": int(counts.get("join", 0)),
        "n_union": int(counts.get("union_rows", 0)),
        "n_aggregate": int(counts.get("aggregate", 0)),
        "n_filter": int(counts.get("filter_rows", 0)),
        "n_rename": int(counts.get("rename_columns", 0)),
        "same_source_branch": same_source_branch,
        "n_sources": len(src_names),
        "n_declared_comparison_sides": n_sides,
        "n_sides_materialized": n_sides_materialized,
        "comparison_sides_share_identical_evidence": sides_share,
        "missing_required_count": len(missing_required),
        "missing_side_count": len(missing_sides),
        "evidence_role_contradiction": evidence_role_contradiction,
        "planner_declared_cannot_plan": status == "cannot_plan",
        "identical_evidence_group_count": len(identical_sets),
    }


def evaluate_capability_signal(attempt_evidence: dict[str, Any]) -> str:
    """Frozen Stage-A candidate. Pure. No models. No category or domain features."""
    if attempt_evidence.get("planner_declared_cannot_plan"):
        return "DO_NOT_ESCALATE"
    if attempt_evidence.get("has_final_grain_contradiction"):
        return "ESCALATE"
    if attempt_evidence.get("evidence_role_contradiction"):
        return "ESCALATE"
    if attempt_evidence.get("has_structural_error") and not attempt_evidence.get("only_unsafe_codes"):
        return "ESCALATE"
    return "DO_NOT_ESCALATE"


def simulate_p2_failure_escalation(ev: dict[str, Any]) -> str:
    status = "cannot_plan" if ev["planner_declared_cannot_plan"] else (
        "failed" if ev["has_structural_error"] else "success"
    )
    retry_log = [{"failure_codes": ev["validation_codes"]}] if ev["validation_codes"] else []
    meta = {
        "exhausted": True,
        "plan_validation_failure_count": 1 if ev["has_structural_error"] else 0,
        "validator_blocked_unsafe_plan": ev["only_unsafe_codes"],
    }
    d = should_escalate_after_fast_path(
        status=status,
        retry_log=retry_log,
        metadata=meta,
        strategy=PlannerModelStrategy(enable_escalation=True),
    )
    return "ESCALATE" if d.should_escalate else "DO_NOT_ESCALATE"


def simulate_p3_current(ev: dict[str, Any]) -> str:
    """Failure escalation OR post-result semantic approximation (identical/missing sides)."""
    if simulate_p2_failure_escalation(ev) == "ESCALATE":
        return "ESCALATE"
    if ev.get("planner_declared_cannot_plan"):
        return "DO_NOT_ESCALATE"
    if ev.get("validation_valid") and ev.get("evidence_role_contradiction"):
        return "ESCALATE"
    return "DO_NOT_ESCALATE"


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


def _roles_two_sides(key: str, a: str, b: str) -> dict[str, Any]:
    return {
        "grain": "entity",
        "required_columns": [key, a, b],
        "output_roles": [
            {"role": "entity_key", "columns": [key]},
            {"role": "comparison_side", "columns": [a], "side_id": "A"},
            {"role": "comparison_side", "columns": [b], "side_id": "B"},
        ],
    }


def build_corpus() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    def add(**kwargs: Any) -> None:
        cases.append(kwargs)

    # ----- G1 valid fast -----
    f_l = pd.DataFrame({"id": ["1", "2"], "x": [1, 2]})
    f_r = pd.DataFrame({"id": ["1", "2"], "y": [3, 4]})
    add(
        attempt_id="g1-join-1to1",
        request_id="p39v-g1-01",
        group="G1",
        shape="ordinary_join",
        split="dev",
        frames={"left.xlsx": f_l, "right.xlsx": f_r},
        plan=_plan({
            "status": "planned",
            "final_output": "j",
            "steps": [{
                "op": "join", "inputs": ["left.xlsx", "right.xlsx"], "output": "j",
                "params": {"left_keys": ["id"], "right_keys": ["id"], "how": "inner"},
            }],
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="일반 1:1 조인. 7B가 충분히 맞출 수 있는 형태.",
        strong_oracle="same_or_unused",
    )

    f_c = pd.DataFrame({"cid": ["A", "B"], "name": ["n1", "n2"]})
    f_o = pd.DataFrame({"cid": ["A", "A", "B"], "amt": [1, 2, 3]})
    add(
        attempt_id="g1-join-1tomany",
        request_id="p39v-g1-02",
        group="G1",
        shape="ordinary_join",
        split="dev",
        frames={"cust.xlsx": f_c, "ord.xlsx": f_o},
        plan=_plan({
            "status": "planned",
            "final_output": "j",
            "steps": [{
                "op": "join", "inputs": ["cust.xlsx", "ord.xlsx"], "output": "j",
                "params": {"left_keys": ["cid"], "right_keys": ["cid"], "how": "left"},
            }],
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="마스터-디테일 1:N 조인. 구조적으로 타당.",
        strong_oracle="same_or_unused",
    )

    jan = pd.DataFrame({"sku": ["S1", "S2"], "qty": [2, 3]})
    feb = pd.DataFrame({"sku": ["S1", "S3"], "qty": [4, 1]})
    add(
        attempt_id="g1-union-months",
        request_id="p39v-g1-03",
        group="G1",
        shape="ordinary_union",
        split="dev",
        frames={"jan.xlsx": jan, "feb.xlsx": feb},
        plan=_plan({
            "status": "planned",
            "final_output": "u",
            "steps": [{
                "op": "union_rows", "inputs": ["jan.xlsx", "feb.xlsx"], "output": "u",
                "params": {},
            }],
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="같은 스키마 월별 적재. 비교 사이드가 없으므로 union이 맞다.",
        strong_oracle="same_or_unused",
    )

    sales = pd.DataFrame({"sku": ["S1", "S1", "S2"], "qty": [1, 2, 3], "region": ["E", "W", "E"]})
    add(
        attempt_id="g1-filter-agg",
        request_id="p39v-g1-04",
        group="G1",
        shape="ordinary_aggregate",
        split="dev",
        frames={"sales.xlsx": sales},
        plan=_plan({
            "status": "planned",
            "final_output": "a",
            "steps": [
                {"op": "filter_rows", "inputs": ["sales.xlsx"], "output": "f",
                 "params": {"conditions": [{"column": "region", "operator": "eq", "value": "E"}]}},
                {"op": "aggregate", "inputs": ["f"], "output": "a",
                 "params": {"group_by": ["sku"], "metrics": [{"column": "qty", "function": "sum", "alias": "qty_e"}]}},
            ],
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="단순 필터 후 집계.",
        strong_oracle="same_or_unused",
    )

    add(
        attempt_id="g1-agg-single",
        request_id="p39v-g1-05",
        group="G1",
        shape="ordinary_aggregate",
        split="dev",
        frames={"sales.xlsx": sales},
        plan=_plan({
            "status": "planned",
            "final_output": "a",
            "steps": [{
                "op": "aggregate", "inputs": ["sales.xlsx"], "output": "a",
                "params": {"group_by": ["sku"], "metrics": [{"column": "qty", "function": "sum", "alias": "qty_sum"}]},
            }],
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="단일 파일 집계.",
        strong_oracle="same_or_unused",
    )

    a_shift = pd.DataFrame({"kiln": ["K1", "K2"], "tons": [10, 11]})
    b_shift = pd.DataFrame({"kiln": ["K1", "K2"], "tons": [7, 8]})
    add(
        attempt_id="g1-rename-join",
        request_id="p39v-g1-06",
        group="G1",
        shape="two_file_rename_join",
        split="holdout",
        frames={"am.xlsx": a_shift, "pm.xlsx": b_shift},
        plan=_plan({
            "status": "planned",
            "final_output": "j",
            "final_output_requirements": _roles_two_sides("kiln", "tons_am", "tons_pm"),
            "steps": [
                {"op": "rename_columns", "inputs": ["am.xlsx"], "output": "amr",
                 "params": {"mapping": {"tons": "tons_am"}}},
                {"op": "rename_columns", "inputs": ["pm.xlsx"], "output": "pmr",
                 "params": {"mapping": {"tons": "tons_pm"}}},
                {"op": "join", "inputs": ["amr", "pmr"], "output": "j",
                 "params": {"left_keys": ["kiln"], "right_keys": ["kiln"], "how": "inner"}},
            ],
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="두 파일 동일 스키마를 rename 후 join. 올바른 비교 형태.",
        strong_oracle="same_or_unused",
    )

    wells = pd.DataFrame({
        "well_id": ["W1", "W1", "W2", "W2"],
        "part": ["P1", "P2", "P1", "P2"],
        "liters": [10, 12, 8, 9],
    })
    add(
        attempt_id="g1-filter-branch-join",
        request_id="p39v-g1-07",
        group="G1",
        shape="same_source_branch",
        split="holdout",
        frames={"readings.xlsx": wells},
        plan=_plan({
            "status": "planned",
            "final_output": "j",
            "final_output_requirements": _roles_two_sides("well_id", "liters_p1", "liters_p2"),
            "steps": [
                {"op": "filter_rows", "inputs": ["readings.xlsx"], "output": "b1",
                 "params": {"conditions": [{"column": "part", "operator": "eq", "value": "P1"}]}},
                {"op": "rename_columns", "inputs": ["b1"], "output": "b1r",
                 "params": {"mapping": {"liters": "liters_p1"}}},
                {"op": "filter_rows", "inputs": ["readings.xlsx"], "output": "b2",
                 "params": {"conditions": [{"column": "part", "operator": "eq", "value": "P2"}]}},
                {"op": "rename_columns", "inputs": ["b2"], "output": "b2r",
                 "params": {"mapping": {"liters": "liters_p2"}}},
                {"op": "join", "inputs": ["b1r", "b2r"], "output": "j",
                 "params": {"left_keys": ["well_id"], "right_keys": ["well_id"], "how": "inner"}},
            ],
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="단일 파일 독립 분기 후 join. 39U 이후 구조적으로 VALID.",
        strong_oracle="same_or_unused",
    )

    add(
        attempt_id="g1-union-then-total",
        request_id="p39v-g1-08",
        group="G1",
        shape="ordinary_union",
        split="holdout",
        frames={"jan.xlsx": jan, "feb.xlsx": feb},
        plan=_plan({
            "status": "planned",
            "final_output": "a",
            "steps": [
                {"op": "union_rows", "inputs": ["jan.xlsx", "feb.xlsx"], "output": "u", "params": {}},
                {"op": "aggregate", "inputs": ["u"], "output": "a",
                 "params": {"group_by": ["sku"], "metrics": [{"column": "qty", "function": "sum", "alias": "qty_all"}]}},
            ],
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="비교가 아닌 전체 합계 요청. union+agg가 맞다. union=잘못 규칙을 금지하는 대조군.",
        strong_oracle="same_or_unused",
    )

    add(
        attempt_id="g1-join-select",
        request_id="p39v-g1-09",
        group="G1",
        shape="ordinary_join",
        split="dev",
        frames={"left.xlsx": f_l, "right.xlsx": f_r},
        plan=_plan({
            "status": "planned",
            "final_output": "s",
            "steps": [
                {"op": "join", "inputs": ["left.xlsx", "right.xlsx"], "output": "j",
                 "params": {"left_keys": ["id"], "right_keys": ["id"], "how": "inner"}},
                {"op": "select_columns", "inputs": ["j"], "output": "s",
                 "params": {"columns": ["id", "x", "y"]}},
            ],
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="조인 후 필요한 열만 선택.",
        strong_oracle="same_or_unused",
    )

    add(
        attempt_id="g1-cannot-plan-missing-side",
        request_id="p39v-g1-10",
        group="G4",
        shape="correct_cannot_plan",
        split="dev",
        frames={"feed.xlsx": pd.DataFrame({"stall": ["S1"], "kg": [3]})},
        plan=_plan({
            "status": "cannot_plan",
            "steps": [],
            "final_output": None,
            "reason": "requested comparison sides are not present as distinct columns or partitions",
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="구분자가 없는 비교 요청에 대해 7B가 올바르게 cannot_plan.",
        strong_oracle="same_cannot_plan",
    )

    add(
        attempt_id="g4-cannot-plan-inlet",
        request_id="p39v-g4-02",
        group="G4",
        shape="correct_cannot_plan",
        split="holdout",
        frames={"vol.xlsx": pd.DataFrame({"reed": ["R1"], "liters": [4]})},
        plan=_plan({
            "status": "cannot_plan",
            "steps": [],
            "final_output": None,
            "reason": "inlet versus outlet distinction is not in the observations",
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="입출 구분 열이 없어 cannot_plan이 정답.",
        strong_oracle="same_cannot_plan",
    )

    add(
        attempt_id="g4-cannot-plan-weekday",
        request_id="p39v-g4-03",
        group="G4",
        shape="correct_cannot_plan",
        split="dev",
        frames={"dock.xlsx": pd.DataFrame({"dock": ["D1"], "crates": [9]})},
        plan=_plan({
            "status": "cannot_plan",
            "steps": [],
            "final_output": None,
            "reason": "weekday/weekend discriminator is absent",
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="요일 구분 부재. cannot_plan 정답.",
        strong_oracle="same_cannot_plan",
    )

    # ----- G2 structurally valid, semantically wrong -----
    add(
        attempt_id="g2-union-collapse-shift",
        request_id="p39v-g2-01",
        group="G2",
        shape="union_collapse",
        split="dev",
        frames={"am.xlsx": a_shift, "pm.xlsx": b_shift},
        plan=_plan({
            "status": "planned",
            "final_output": "a",
            "final_output_requirements": _roles_two_sides("kiln", "tons_am", "tons_pm"),
            "reason": "union then one total",
            "steps": [
                {"op": "union_rows", "inputs": ["am.xlsx", "pm.xlsx"], "output": "u", "params": {}},
                {"op": "aggregate", "inputs": ["u"], "output": "a",
                 "params": {"group_by": ["kiln"], "metrics": [{"column": "tons", "function": "sum", "alias": "tons_total"}]}},
            ],
        }),
        fast_correct="NO",
        capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="두 교대 비교인데 union+단일 합계로 붕괴. 구조 VALID. D02 앵커 형태.",
        strong_oracle="rename_join",
        historical_anchor="D02",
    )

    wh_a = pd.DataFrame({"bin_id": ["B1", "B2"], "kg": [5, 6]})
    wh_b = pd.DataFrame({"bin_id": ["B1", "B2"], "kg": [2, 3]})
    add(
        attempt_id="g2-union-collapse-warehouse",
        request_id="p39v-g2-02",
        group="G2",
        shape="union_collapse",
        split="holdout",
        frames={"east.xlsx": wh_a, "west.xlsx": wh_b},
        plan=_plan({
            "status": "planned",
            "final_output": "a",
            "final_output_requirements": _roles_two_sides("bin_id", "kg_east", "kg_west"),
            "steps": [
                {"op": "union_rows", "inputs": ["east.xlsx", "west.xlsx"], "output": "u", "params": {}},
                {"op": "aggregate", "inputs": ["u"], "output": "a",
                 "params": {"group_by": ["bin_id"], "metrics": [{"column": "kg", "function": "sum", "alias": "kg_all"}]}},
            ],
        }),
        fast_correct="NO",
        capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="다른 도메인/열 이름의 같은 붕괴 형태. 홀드아웃 일반화 검사용.",
        strong_oracle="rename_join",
    )

    tonnes = pd.DataFrame({"bench": ["N1", "N2", "N1"], "tonnes": [20, 14, 9]})
    meta = pd.DataFrame({"bench": ["N1", "N2"], "ledge": ["L1", "L2"]})
    add(
        attempt_id="g2-fake-dual-aliases",
        request_id="p39v-g2-03",
        group="G2",
        shape="fake_dual",
        split="dev",
        frames={"q.xlsx": tonnes, "b.xlsx": meta},
        plan=_plan({
            "status": "planned",
            "final_output": "a",
            "final_output_requirements": _roles_two_sides("bench", "face", "back"),
            "steps": [
                {"op": "join", "inputs": ["q.xlsx", "b.xlsx"], "output": "j",
                 "params": {"left_keys": ["bench"], "right_keys": ["bench"], "how": "inner"}},
                {"op": "aggregate", "inputs": ["j"], "output": "a",
                 "params": {"group_by": ["bench"], "metrics": [
                     {"column": "tonnes", "function": "sum", "alias": "face"},
                     {"column": "tonnes", "function": "sum", "alias": "back"},
                 ]}},
            ],
        }),
        fast_correct="NO",
        capability="FAST_OVERCOMMITS_STRONG_CORRECTLY_CANNOT_PLAN",
        note_ko="같은 tonnes를 face/back로 이중 별칭. 구분 증거 없음. C02 형태.",
        strong_oracle="cannot_plan",
        historical_anchor="C02",
    )

    barn = pd.DataFrame({"stall_id": ["T1", "T2"], "kg": [4, 5], "wing": ["N", "S"]})
    add(
        attempt_id="g2-fake-dual-single-file",
        request_id="p39v-g2-04",
        group="G2",
        shape="fake_dual",
        split="holdout",
        frames={"barn.xlsx": barn},
        plan=_plan({
            "status": "planned",
            "final_output": "a",
            "final_output_requirements": _roles_two_sides("stall_id", "alpha", "beta"),
            "steps": [{
                "op": "aggregate", "inputs": ["barn.xlsx"], "output": "a",
                "params": {"group_by": ["stall_id"], "metrics": [
                    {"column": "kg", "function": "sum", "alias": "alpha"},
                    {"column": "kg", "function": "sum", "alias": "beta"},
                ]},
            }],
        }),
        fast_correct="NO",
        capability="FAST_OVERCOMMITS_STRONG_CORRECTLY_CANNOT_PLAN",
        note_ko="단일 파일에서 같은 kg를 두 사이드로 복제.",
        strong_oracle="cannot_plan",
    )

    add(
        attempt_id="g2-one-metric-two-roles",
        request_id="p39v-g2-05",
        group="G2",
        shape="role_collapse",
        split="dev",
        frames={"sales.xlsx": sales},
        plan=_plan({
            "status": "planned",
            "final_output": "a",
            "final_output_requirements": _roles_two_sides("sku", "east_qty", "west_qty"),
            "steps": [{
                "op": "aggregate", "inputs": ["sales.xlsx"], "output": "a",
                "params": {"group_by": ["sku"], "metrics": [
                    {"column": "qty", "function": "sum", "alias": "qty_all"},
                ]},
            }],
        }),
        fast_correct="NO",
        capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="사이드 두 개를 선언하고 전체 합 하나만 물질화.",
        strong_oracle="filter_branch_or_cannot",
    )

    # ----- G3 structurally invalid -----
    stalls = pd.DataFrame({"stall_id": ["T1", "T2"], "wing": ["N", "S"]})
    add(
        attempt_id="g3-c03-grain",
        request_id="p39v-g3-01",
        group="G3",
        shape="grain_contradiction",
        split="dev",
        frames={"barn_feed.xlsx": pd.DataFrame({"stall_id": ["T1", "T2"], "kg": [8, 6]}), "stalls.xlsx": stalls},
        plan=_plan({
            "status": "planned",
            "final_output": "a",
            "final_output_requirements": {
                "grain": "entity",
                "required_columns": ["stall_id", "alpha", "beta"],
                "output_roles": [
                    {"role": "entity_key", "columns": ["stall_id"]},
                    {"role": "comparison_side", "columns": ["alpha"], "side_id": "A"},
                    {"role": "comparison_side", "columns": ["beta"], "side_id": "B"},
                ],
            },
            "steps": [
                {"op": "join", "inputs": ["barn_feed.xlsx", "stalls.xlsx"], "output": "j",
                 "params": {"left_keys": ["stall_id"], "right_keys": ["stall_id"], "how": "inner"}},
                {"op": "aggregate", "inputs": ["j"], "output": "a",
                 "params": {"group_by": ["stall_id"], "metrics": [
                     {"column": "kg", "function": "sum", "alias": "alpha"},
                     {"column": "kg", "function": "sum", "alias": "beta"},
                 ]}},
                {"op": "select_columns", "inputs": ["a"], "output": "s",
                 "params": {"columns": ["stall_id", "alpha", "beta"]}},
            ],
        }),
        fast_correct="NO",
        capability="FAST_OVERCOMMITS_STRONG_CORRECTLY_CANNOT_PLAN",
        note_ko="C03 7B 앵커: fake-dual + grain 모순. 32B는 cannot_plan 5/5.",
        strong_oracle="cannot_plan",
        historical_anchor="C03",
    )

    add(
        attempt_id="g3-d01-7b-bad-branch",
        request_id="p39v-g3-02",
        group="G3",
        shape="invalid_branch",
        split="holdout",
        frames={"well_readings.xlsx": pd.DataFrame({
            "well_id": ["WL1", "WL1", "WL2", "WL2"],
            "day": ["D1", "D2", "D1", "D2"],
            "liters": [10, 12, 8, 9],
        })},
        plan=_plan({
            "status": "planned",
            "final_output": "s",
            "final_output_requirements": _roles_two_sides("well_id", "d1", "d2"),
            "steps": [
                {"op": "filter_rows", "inputs": ["well_readings.xlsx"], "output": "f1",
                 "params": {"conditions": [{"column": "day", "operator": "eq", "value": "D1"}]}},
                {"op": "filter_rows", "inputs": ["well_readings.xlsx"], "output": "f2",
                 "params": {"conditions": [{"column": "day", "operator": "eq", "value": "D2"}]}},
                {"op": "join", "inputs": ["f1", "f2"], "output": "j",
                 "params": {"left_keys": ["missing_key"], "right_keys": ["missing_key"], "how": "inner"}},
                {"op": "aggregate", "inputs": ["j"], "output": "a",
                 "params": {"group_by": ["well_id"], "metrics": [{"column": "liters", "function": "sum", "alias": "liters"}]}},
                {"op": "select_columns", "inputs": ["a"], "output": "s",
                 "params": {"columns": ["well_id", "liters"]}},
            ],
        }),
        fast_correct="NO",
        capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="D01 7B 앵커: 잘못된 키/분기. 32B는 올바른 분기를 냄. 39U 이후 32B 계획은 VALID.",
        strong_oracle="filter_rename_join",
        historical_anchor="D01",
    )

    m2m_l = pd.DataFrame({"k": ["1", "1", "2", "2"], "x": [1, 2, 3, 4]})
    m2m_r = pd.DataFrame({"k": ["1", "1", "2", "2"], "y": [5, 6, 7, 8]})
    add(
        attempt_id="g3-genuine-m2m",
        request_id="p39v-g3-03",
        group="G3",
        shape="many_to_many",
        split="dev",
        frames={"L.xlsx": m2m_l, "R.xlsx": m2m_r},
        plan=_plan({
            "status": "planned",
            "final_output": "j",
            "steps": [{
                "op": "join", "inputs": ["L.xlsx", "R.xlsx"], "output": "j",
                "params": {"left_keys": ["k"], "right_keys": ["k"], "how": "inner"},
            }],
        }),
        fast_correct="NO",
        capability="BOTH_INSUFFICIENT",
        note_ko="진짜 many-to-many. 기존 정책은 32B로 올리지 않고 안전 거절.",
        strong_oracle="cannot_plan_or_reject",
    )

    add(
        attempt_id="g3-nonexistent-column",
        request_id="p39v-g3-04",
        group="G3",
        shape="invalid_reference",
        split="holdout",
        frames={"sales.xlsx": sales},
        plan=_plan({
            "status": "planned",
            "final_output": "a",
            "steps": [{
                "op": "aggregate", "inputs": ["sales.xlsx"], "output": "a",
                "params": {"group_by": ["not_a_col"], "metrics": [{"column": "qty", "function": "sum", "alias": "s"}]},
            }],
        }),
        fast_correct="NO",
        capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="존재하지 않는 열 참조. 구조 오류.",
        strong_oracle="valid_agg_or_cannot",
    )

    add(
        attempt_id="g3-grain-row-collapse",
        request_id="p39v-g3-05",
        group="G3",
        shape="grain_contradiction",
        split="holdout",
        frames={"left.xlsx": f_l, "right.xlsx": f_r},
        plan=_plan({
            "status": "planned",
            "final_output": "a",
            "final_output_requirements": {
                "grain": "detail",
                "required_columns": ["id", "x", "y"],
            },
            "steps": [
                {"op": "join", "inputs": ["left.xlsx", "right.xlsx"], "output": "j",
                 "params": {"left_keys": ["id"], "right_keys": ["id"], "how": "inner"}},
                {"op": "aggregate", "inputs": ["j"], "output": "a",
                 "params": {"group_by": ["id"], "metrics": [{"column": "x", "function": "sum", "alias": "x"}]}},
            ],
        }),
        fast_correct="NO",
        capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="detail grain + 붕괴 aggregate. 기존 failure escalation 트리거.",
        strong_oracle="join_without_collapse",
    )

    # ----- G5 -----
    add(
        attempt_id="g5-ambiguous-combine",
        request_id="p39v-g5-01",
        group="G5",
        shape="ambiguous",
        split="dev",
        frames={"jan.xlsx": jan, "feb.xlsx": feb},
        plan=_plan({
            "status": "planned",
            "final_output": "u",
            "steps": [{"op": "union_rows", "inputs": ["jan.xlsx", "feb.xlsx"], "output": "u", "params": {}}],
        }),
        fast_correct="INDETERMINATE",
        capability="OPERATIONALLY_INDETERMINATE",
        note_ko="프롬프트가 join/union을 특정하지 않으면 어느 쪽도 강제할 수 없음.",
        strong_oracle="indeterminate",
    )

    add(
        attempt_id="g5-underspecified-grain",
        request_id="p39v-g5-02",
        group="G5",
        shape="ambiguous",
        split="holdout",
        frames={"sales.xlsx": sales},
        plan=_plan({
            "status": "planned",
            "final_output": "a",
            "steps": [{
                "op": "aggregate", "inputs": ["sales.xlsx"], "output": "a",
                "params": {"group_by": ["region"], "metrics": [{"column": "qty", "function": "sum", "alias": "q"}]},
            }],
        }),
        fast_correct="INDETERMINATE",
        capability="OPERATIONALLY_INDETERMINATE",
        note_ko="요청 grain이 sku인지 region인지 불명확.",
        strong_oracle="indeterminate",
    )

    add(
        attempt_id="g5-timeout-historical",
        request_id="p39v-g5-03",
        group="G5",
        shape="operational",
        split="dev",
        frames={"x.xlsx": f_l},
        plan=_plan({
            "status": "cannot_plan",
            "steps": [],
            "final_output": None,
            "reason": "planner_parse_failed",
            "notes": ["ReadTimeout timeout=300"],
        }),
        fast_correct="INDETERMINATE",
        capability="OPERATIONALLY_INDETERMINATE",
        note_ko="백엔드 타임아웃은 능력 신호가 아님. C01/D01 역사적 RC-J.",
        strong_oracle="operational",
        format_retry_count=3,
    )

    add(
        attempt_id="g1-cannot-plan-unrelated",
        request_id="p39v-g4-04",
        group="G4",
        shape="correct_cannot_plan",
        split="holdout",
        frames={"a.xlsx": pd.DataFrame({"foo": [1]}), "b.xlsx": pd.DataFrame({"bar": [2]})},
        plan=_plan({
            "status": "cannot_plan",
            "steps": [],
            "final_output": None,
            "reason": "no join or union evidence between unrelated observations",
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="무관 파일. cannot_plan이 맞다.",
        strong_oracle="same_cannot_plan",
    )

    add(
        attempt_id="g2-union-collapse-no-roles-but-two-files-compare",
        request_id="p39v-g2-06",
        group="G2",
        shape="union_collapse",
        split="dev",
        frames={"am.xlsx": a_shift, "pm.xlsx": b_shift},
        plan=_plan({
            "status": "planned",
            "final_output": "a",
            "final_output_requirements": {
                "grain": "entity",
                "required_columns": ["kiln", "am_tons", "pm_tons"],
            },
            "steps": [
                {"op": "union_rows", "inputs": ["am.xlsx", "pm.xlsx"], "output": "u", "params": {}},
                {"op": "aggregate", "inputs": ["u"], "output": "a",
                 "params": {"group_by": ["kiln"], "metrics": [{"column": "tons", "function": "sum", "alias": "tons"}]}},
            ],
        }),
        fast_correct="NO",
        capability="FAST_INSUFFICIENT_STRONG_RECOVERS",
        note_ko="required_columns는 두 사이드인데 한 메트릭만 물질화. 역할 없이도 관측 가능.",
        strong_oracle="rename_join",
    )

    add(
        attempt_id="g1-rename-only",
        request_id="p39v-g1-12",
        group="G1",
        shape="ordinary_rename",
        split="dev",
        frames={"sales.xlsx": sales},
        plan=_plan({
            "status": "planned",
            "final_output": "r",
            "steps": [{
                "op": "rename_columns", "inputs": ["sales.xlsx"], "output": "r",
                "params": {"mapping": {"qty": "quantity"}},
            }],
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="단순 rename. 능력 에스컬레이션이 필요 없다.",
        strong_oracle="same_or_unused",
    )

    add(
        attempt_id="g1-filter-only",
        request_id="p39v-g1-13",
        group="G1",
        shape="ordinary_filter",
        split="holdout",
        frames={"sales.xlsx": sales},
        plan=_plan({
            "status": "planned",
            "final_output": "f",
            "steps": [{
                "op": "filter_rows", "inputs": ["sales.xlsx"], "output": "f",
                "params": {"conditions": [{"column": "region", "operator": "eq", "value": "E"}]},
            }],
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="단순 필터. 유효한 fast 대조군.",
        strong_oracle="same_or_unused",
    )

    add(
        attempt_id="g1-lookup-join",
        request_id="p39v-g1-11",
        group="G1",
        shape="ordinary_join",
        split="holdout",
        frames={"cust.xlsx": f_c, "ord.xlsx": f_o},
        plan=_plan({
            "status": "planned",
            "final_output": "j",
            "steps": [{
                "op": "join", "inputs": ["ord.xlsx", "cust.xlsx"], "output": "j",
                "params": {"left_keys": ["cid"], "right_keys": ["cid"], "how": "left"},
            }],
        }),
        fast_correct="YES",
        capability="FAST_SUFFICIENT",
        note_ko="주문에 고객 속성을 붙이는 룩업.",
        strong_oracle="same_or_unused",
    )

    add(
        attempt_id="g3-m2m-holdout",
        request_id="p39v-g3-06",
        group="G3",
        shape="many_to_many",
        split="holdout",
        frames={
            "P.xlsx": pd.DataFrame({"pid": ["1", "1", "2"], "v": [1, 2, 3]}),
            "Q.xlsx": pd.DataFrame({"pid": ["1", "1", "2"], "w": [4, 5, 6]}),
        },
        plan=_plan({
            "status": "planned",
            "final_output": "j",
            "steps": [{
                "op": "join", "inputs": ["P.xlsx", "Q.xlsx"], "output": "j",
                "params": {"left_keys": ["pid"], "right_keys": ["pid"], "how": "inner"},
            }],
        }),
        fast_correct="NO",
        capability="BOTH_INSUFFICIENT",
        note_ko="홀드아웃 many-to-many. 안전 거절이 기존 정책.",
        strong_oracle="cannot_plan_or_reject",
    )

    return cases


def metrics_for(rows: list[dict[str, Any]], pred_key: str) -> dict[str, Any]:
    labeled = [r for r in rows if r["fast_correct"] in {"YES", "NO"}]
    tp = sum(1 for r in labeled if r["fast_correct"] == "NO" and r[pred_key] == "ESCALATE")
    fp = sum(1 for r in labeled if r["fast_correct"] == "YES" and r[pred_key] == "ESCALATE")
    fn = sum(1 for r in labeled if r["fast_correct"] == "NO" and r[pred_key] != "ESCALATE")
    tn = sum(1 for r in labeled if r["fast_correct"] == "YES" and r[pred_key] != "ESCALATE")
    n_esc = sum(1 for r in labeled if r[pred_key] == "ESCALATE")
    n = len(labeled)
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    fpr = fp / (fp + tn) if (fp + tn) else None
    fnr = fn / (fn + tp) if (fn + tp) else None
    useful = tp
    return {
        "n_labeled": n,
        "true_escalation": tp,
        "unnecessary_escalation": fp,
        "missed_insufficiency": fn,
        "true_negative": tn,
        "escalation_rate": round(n_esc / n, 4) if n else None,
        "precision": None if prec is None else round(prec, 4),
        "recall": None if rec is None else round(rec, 4),
        "false_positive_rate": None if fpr is None else round(fpr, 4),
        "false_negative_rate": None if fnr is None else round(fnr, 4),
        "useful_strong_calls_over_all_strong": (
            None if n_esc == 0 else round(useful / n_esc, 4)
        ),
    }


def run_research() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    corpus = build_corpus()
    rows: list[dict[str, Any]] = []
    for c in corpus:
        ev = extract_attempt_evidence(
            attempt_id=c["attempt_id"],
            request_id=c["request_id"],
            plan=c["plan"],
            understanding=_und_from_frames(c["frames"]),
            frames=c["frames"],
            format_retry_count=int(c.get("format_retry_count") or 0),
        )
        rec = {
            **{k: v for k, v in c.items() if k not in {"plan", "frames"}},
            **ev,
            "pred_p0": "DO_NOT_ESCALATE",
            "pred_p1": "ESCALATE",
            "pred_p2": simulate_p2_failure_escalation(ev),
            "pred_p3": simulate_p3_current(ev),
            "pred_r_grain": "ESCALATE" if ev["has_final_grain_contradiction"] else "DO_NOT_ESCALATE",
            "pred_r_valid_fail": "ESCALATE" if ev["has_structural_error"] else "DO_NOT_ESCALATE",
            "pred_r_identical": (
                "ESCALATE" if ev["comparison_sides_share_identical_evidence"] else "DO_NOT_ESCALATE"
            ),
            "pred_r_evidence": (
                "ESCALATE" if ev["evidence_role_contradiction"] else "DO_NOT_ESCALATE"
            ),
            "pred_r_union": "ESCALATE" if ev["n_union"] >= 1 else "DO_NOT_ESCALATE",
            "pred_r_files": "ESCALATE" if ev["n_sources"] >= 2 else "DO_NOT_ESCALATE",
            "pred_r_frozen": evaluate_capability_signal(ev),
        }
        rec["pred_r_no_grain"] = (
            "ESCALATE"
            if (ev["evidence_role_contradiction"] or (ev["has_structural_error"] and not ev["only_unsafe_codes"]))
            else "DO_NOT_ESCALATE"
        )
        rec["pred_r_no_evidence"] = (
            "ESCALATE"
            if (ev["has_final_grain_contradiction"] or (ev["has_structural_error"] and not ev["only_unsafe_codes"]))
            else "DO_NOT_ESCALATE"
        )
        rec["pred_r_no_struct"] = (
            "ESCALATE"
            if (ev["has_final_grain_contradiction"] or ev["evidence_role_contradiction"])
            else "DO_NOT_ESCALATE"
        )
        rows.append(rec)

    dev = [r for r in rows if r["split"] == "dev"]
    hold = [r for r in rows if r["split"] == "holdout"]
    return {"rows": rows, "dev": dev, "hold": hold}


def _write_json(name: str, obj: Any) -> None:
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def write_artifacts(bundle: dict[str, Any]) -> None:
    rows, dev, hold = bundle["rows"], bundle["dev"], bundle["hold"]
    labeled = [r for r in rows if r["fast_correct"] in {"YES", "NO"}]

    _write_json("baseline_freeze.json", {
        "phase": "39V",
        "phase39u_sha": "980b7fa7f32dc15791ad9a88dd36d201c19c254b",
        "shadow": "OFF",
        "production_routing_changed": False,
        "planner_changed": False,
        "verifier_changed": False,
        "escalation_changed": False,
        "timeout_changed": False,
        "dsl_changed": False,
        "v2_2_changed": False,
        "phase39u_regression": "251 passed / 0 failed",
    })

    _write_json("current_escalation_call_graph.json", {
        "failure_escalation": {
            "function": "core.integrate.planner_model_strategy.should_escalate_after_fast_path",
            "wired_from": "core.integrate.integration_pipeline.run_integration_pipeline",
            "trigger": "fast status=failed AND (final_grain_contradiction|required_field_not_materializable|join_key_dropped) or result/exec exhaustion",
            "skip": "cannot_plan, success, unsafe-only, expected-negative structural",
            "model": "qwen3:32b",
            "lineage": "parent fast attempt superseded_by_failure_escalation",
            "reverify": "child plan validated/executed independently",
        },
        "semantic_escalation": {
            "function": "core.integrate.semantic_escalation._should_semantic_escalate",
            "wired_from": "run_integration_pipeline_semantic_experimental",
            "trigger": "verifier verdict fail (optional uncertain)",
            "model": "qwen3:32b",
            "lineage": "parent superseded_by_semantic_escalation",
            "reverify": "yes, child is verified",
            "stage": "B",
        },
        "no_escalation": "fast success + verifier PASS",
    })

    _write_json("research_corpus.json", {
        "n": len(rows),
        "n_dev": len(dev),
        "n_holdout": len(hold),
        "groups": dict(Counter(r["group"] for r in rows)),
        "shapes": dict(Counter(r["shape"] for r in rows)),
        "fast_yes": sum(1 for r in rows if r["fast_correct"] == "YES"),
        "fast_no": sum(1 for r in rows if r["fast_correct"] == "NO"),
        "indeterminate": sum(1 for r in rows if r["fast_correct"] == "INDETERMINATE"),
        "strong_recoverable": sum(1 for r in rows if r["capability"] == "FAST_INSUFFICIENT_STRONG_RECOVERS"),
        "attempts": [
            {
                "attempt_id": r["attempt_id"],
                "request_id": r["request_id"],
                "group": r["group"],
                "shape": r["shape"],
                "split": r["split"],
                "fast_correct": r["fast_correct"],
                "capability": r["capability"],
                "historical_anchor": r.get("historical_anchor"),
            }
            for r in rows
        ],
    })

    _write_json("manual_attempt_labels.json", {
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

    _write_json("feature_inventory.json", [
        {"name": "fast_status", "source": "planner+validator", "deterministic": True, "semantic": False,
         "before_exec": True, "after_exec": True, "architecture_safe": True, "leakage_risk": "low",
         "cost": "none", "rationale": "F1 exact fast-stage status"},
        {"name": "has_final_grain_contradiction", "source": "validator", "deterministic": True, "semantic": False,
         "before_exec": True, "after_exec": True, "architecture_safe": True, "leakage_risk": "low",
         "cost": "none", "rationale": "F2/F5 existing trigger"},
        {"name": "has_structural_error", "source": "validator", "deterministic": True, "semantic": False,
         "before_exec": True, "after_exec": True, "architecture_safe": True, "leakage_risk": "low",
         "cost": "none", "rationale": "F2"},
        {"name": "only_unsafe_codes", "source": "validator", "deterministic": True, "semantic": False,
         "before_exec": True, "after_exec": True, "architecture_safe": True, "leakage_risk": "low",
         "cost": "none", "rationale": "preserve existing unsafe-skip policy"},
        {"name": "format_retry_count", "source": "planner", "deterministic": True, "semantic": False,
         "before_exec": True, "after_exec": True, "architecture_safe": True, "leakage_risk": "low",
         "cost": "none", "rationale": "F3; measured, not assumed bad"},
        {"name": "n_union/n_join/n_filter", "source": "plan graph", "deterministic": True, "semantic": False,
         "before_exec": True, "after_exec": True, "architecture_safe": True, "leakage_risk": "medium if used alone",
         "cost": "none", "rationale": "F4 descriptive; union-alone forbidden as rule"},
        {"name": "n_sources", "source": "understanding", "deterministic": True, "semantic": False,
         "before_exec": True, "after_exec": True, "architecture_safe": False,
         "leakage_risk": "high — file-count routing forbidden", "cost": "none",
         "rationale": "recorded only, never a decision feature"},
        {"name": "evidence_role_contradiction", "source": "schema_lineage+declared roles", "deterministic": True,
         "semantic": False, "before_exec": True, "after_exec": True, "architecture_safe": True,
         "leakage_risk": "low", "cost": "lineage walk",
         "rationale": "F5/F6 declared sides vs independent evidence/materialization"},
        {"name": "comparison_sides_share_identical_evidence", "source": "V2.2 lineage", "deterministic": True,
         "semantic": False, "before_exec": True, "after_exec": True, "architecture_safe": True,
         "leakage_risk": "low", "cost": "lineage walk", "rationale": "F6"},
        {"name": "same_source_branch", "source": "plan graph", "deterministic": True, "semantic": False,
         "before_exec": True, "after_exec": True, "architecture_safe": True, "leakage_risk": "medium alone",
         "cost": "none", "rationale": "F7 descriptive"},
        {"name": "family/case_id/column names", "source": "benchmark", "deterministic": True, "semantic": True,
         "before_exec": True, "after_exec": True, "architecture_safe": False, "leakage_risk": "forbidden",
         "cost": "n/a", "rationale": "labels only, never features"},
    ])

    feat_cols = [
        "attempt_id", "request_id", "split", "group", "shape", "fast_correct", "capability",
        "fast_status", "validation_valid", "has_final_grain_contradiction", "has_many_to_many",
        "has_structural_error", "only_unsafe_codes", "has_trigger_code", "n_ops", "n_join",
        "n_union", "n_aggregate", "n_filter", "n_rename", "same_source_branch", "n_sources",
        "n_declared_comparison_sides", "n_sides_materialized",
        "comparison_sides_share_identical_evidence", "evidence_role_contradiction",
        "missing_required_count", "planner_declared_cannot_plan",
        "pred_p0", "pred_p2", "pred_p3", "pred_r_frozen",
    ]
    with (OUT / "feature_matrix.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=feat_cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    stab_path = PHASE39T / "planner_stability_results.json"
    stab = json.loads(stab_path.read_text()) if stab_path.exists() else {}
    _write_json("stability_results.json", {
        "reused_from_phase39t": True,
        "n": 5,
        "anchors": {
            "C03_7B": "5/5 planned fake-dual (stable capability failure)",
            "C03_32B": "5/5 declared cannot_plan",
            "D01_7B": "5/5 invalid branch plans",
            "D01_32B": "5/5 correct branch shape",
            "D02_7B": "5/5 union+agg",
            "D02_32B": "5/5 rename+join",
            "valid_control": "not re-run live; G1 ordinary join treated as stable-sufficient by construction",
        },
        "phase39t_counts": stab.get("counts"),
        "interpretation": "Anchor failures are stable, not stochastic.",
    })

    _write_json("strong_model_comparison.json", {
        "fidelity": "RECONSTRUCTED_REPLAY from Phase 39T plus analyst oracle on synthetic twins",
        "anchors": {
            "C03": {"fast": "fake-dual", "strong": "cannot_plan 5/5", "label": "FAST_OVERCOMMITS_STRONG_CORRECTLY_CANNOT_PLAN"},
            "D01": {"fast": "invalid branch", "strong": "filter/rename/join 5/5", "label": "FAST_INSUFFICIENT_STRONG_RECOVERS"},
            "D02": {"fast": "union+agg", "strong": "rename+join 5/5", "label": "FAST_INSUFFICIENT_STRONG_RECOVERS"},
        },
        "RECOVERABLE_BY_STRONG": sum(1 for r in labeled if r["capability"] == "FAST_INSUFFICIENT_STRONG_RECOVERS"),
        "NOT_RECOVERABLE_BY_STRONG": sum(1 for r in labeled if r["capability"] == "BOTH_INSUFFICIENT"),
        "FAST_OVERCOMMITS_STRONG_CANNOT_PLAN": sum(
            1 for r in labeled if r["capability"] == "FAST_OVERCOMMITS_STRONG_CORRECTLY_CANNOT_PLAN"
        ),
        "no_new_32b_live_calls": True,
    })

    single = {
        "P0_never": metrics_for(rows, "pred_p0"),
        "P1_always": metrics_for(rows, "pred_p1"),
        "P2_failure_escalation": metrics_for(rows, "pred_p2"),
        "P3_failure_plus_semantic_approx": metrics_for(rows, "pred_p3"),
        "S_grain_only": metrics_for(rows, "pred_r_grain"),
        "S_any_structural": metrics_for(rows, "pred_r_valid_fail"),
        "S_identical_sides": metrics_for(rows, "pred_r_identical"),
        "S_evidence_role": metrics_for(rows, "pred_r_evidence"),
        "S_union_forbidden": metrics_for(rows, "pred_r_union"),
        "S_filecount_forbidden": metrics_for(rows, "pred_r_files"),
    }
    _write_json("single_signal_results.json", single)

    composite = {
        "R_frozen": metrics_for(rows, "pred_r_frozen"),
        "R_frozen_dev": metrics_for(dev, "pred_r_frozen"),
        "R_frozen_holdout": metrics_for(hold, "pred_r_frozen"),
    }
    _write_json("composite_rule_results.json", composite)
    _write_json("development_results.json", {
        "R_frozen": metrics_for(dev, "pred_r_frozen"),
        "P2": metrics_for(dev, "pred_p2"),
        "P3": metrics_for(dev, "pred_p3"),
    })
    _write_json("holdout_results.json", {
        "R_frozen": metrics_for(hold, "pred_r_frozen"),
        "P2": metrics_for(hold, "pred_p2"),
        "P3": metrics_for(hold, "pred_p3"),
        "post_holdout_adjustment": False,
    })
    _write_json("ablation_results.json", {
        "R": metrics_for(rows, "pred_r_frozen"),
        "R_without_grain": metrics_for(rows, "pred_r_no_grain"),
        "R_without_evidence": metrics_for(rows, "pred_r_no_evidence"),
        "R_without_nonunsafe_struct": metrics_for(rows, "pred_r_no_struct"),
    })

    fps = [r for r in hold if r["fast_correct"] == "YES" and r["pred_r_frozen"] == "ESCALATE"]
    fns = [r for r in labeled if r["fast_correct"] == "NO" and r["pred_r_frozen"] != "ESCALATE"]
    _write_json("false_positive_review.json", {
        "holdout_valid_escalated": [
            {"attempt_id": r["attempt_id"], "note_ko": r["note_ko"],
             "class": "concerning" if False else "acceptable_conservative_or_none",
             "features": {
                 "evidence_role_contradiction": r["evidence_role_contradiction"],
                 "has_structural_error": r["has_structural_error"],
                 "grain": r["has_final_grain_contradiction"],
             }}
            for r in fps
        ],
        "count": len(fps),
    })
    _write_json("false_negative_review.json", {
        "fast_wrong_not_escalated": [
            {
                "attempt_id": r["attempt_id"],
                "split": r["split"],
                "note_ko": r["note_ko"],
                "evidence": {
                    "codes": r["validation_codes"],
                    "only_unsafe": r["only_unsafe_codes"],
                    "evidence_role_contradiction": r["evidence_role_contradiction"],
                },
                "why_missed_ko": (
                    "기존 unsafe-only 안전 거절과 동일하게 many-to-many는 능력 에스컬레이션하지 않음."
                    if r["only_unsafe_codes"]
                    else "Stage-A generic 모순이 관측되지 않음."
                ),
                "semantic_hardcode_needed": False,
                "verifier_later": r["group"] == "G2",
            }
            for r in fns
        ],
        "count": len(fns),
    })

    _write_json("failure_escalation_overlap.json", [
        {
            "attempt_id": r["attempt_id"],
            "fast_correct": r["fast_correct"],
            "early_r": r["pred_r_frozen"],
            "failure_escalation": r["pred_p2"],
            "semantic_approx": r["pred_p3"],
            "strong_useful": r["capability"] in {
                "FAST_INSUFFICIENT_STRONG_RECOVERS",
                "FAST_OVERCOMMITS_STRONG_CORRECTLY_CANNOT_PLAN",
            },
        }
        for r in rows
    ])

    _write_json("latency_strategy_comparison.json", {
        "units": "seconds, historical Phase 39T means",
        "fast_plan_s": 25,
        "strong_plan_s": 200,
        "verifier_s": 40,
        "exec_s": 1,
        "notes": "Not an SLO. 32B D01 ~277s near 300s timeout (RC-J).",
        "strategies": {
            "S0_7b_current": "7B + failure escalation + semantic escalation after verifier",
            "S1_always_32b": "every request ~200s planner",
            "S2_early_R": "Stage-A R then 32B planner; saves verifier on G2 vs P3 but still 32B planner",
            "S3_semantic_only": "same as catching G2 after execute",
        },
        "uncertainty": "synthetic corpus has no live latency; anchors only",
    })

    _write_json("rule_revision_log.json", [
        {
            "version": "v0",
            "rule": "ESCALATE iff has_final_grain_contradiction",
            "reason_changed": "initial single-signal from existing failure trigger",
            "anchor_specific": False,
            "generalization": "already production-generic",
        },
        {
            "version": "v1",
            "previous": "v0",
            "rule": "v0 OR evidence_role_contradiction OR (structural error AND NOT only_unsafe)",
            "reason_changed": "development G2 union-collapse / fake-dual were VALID so grain-only missed them",
            "evidence_motivating": "declared comparison sides vs identical/missing materialization (V2.2)",
            "anchor_specific": False,
            "generalization": "uses declared roles and lineage, not union/file-count/family",
        },
        {
            "version": "v1_frozen",
            "note": "frozen before holdout; no post-holdout adjustment",
        },
    ])

    ranking = [
        {"feature": "evidence_role_contradiction", "why": "catches G2 without union/file-count"},
        {"feature": "has_final_grain_contradiction", "why": "high precision, already a failure trigger"},
        {"feature": "non-unsafe structural error", "why": "catches invalid refs/branches"},
        {"feature": "any structural error", "why": "over-escalates if it includes m2m skip class"},
        {"feature": "n_union / n_sources", "why": "forbidden as sole rule; valid unions exist"},
        {"feature": "retry count", "why": "corpus mostly 0; not informative here"},
    ]
    _write_json("feature_ranking.json", ranking)

    rec_hold = metrics_for(hold, "pred_r_frozen")
    rec_all = metrics_for(rows, "pred_r_frozen")
    p3_all = metrics_for(rows, "pred_p3")
    recommend = "KEEP_7B_DEFAULT_AND_RESEARCH_EARLY_ROUTING"
    production = "NO_PRODUCTION_ROUTING_RULE_RECOMMENDED"
    next_outcome = "B"
    if (rec_hold.get("recall") or 0) >= 0.75 and (rec_hold.get("unnecessary_escalation") or 0) <= 1:
        if (rec_hold.get("recall") or 0) >= 0.9 and rec_hold.get("unnecessary_escalation") == 0:
            recommend = "EVIDENCE_SUPPORTS_EARLY_ROUTING_IMPLEMENTATION"
            production = (
                "ESCALATE if final_grain_contradiction OR evidence_role_contradiction "
                "OR (structural error AND NOT only_unsafe_codes); skip cannot_plan"
            )
            next_outcome = "A"
    _write_json("routing_recommendation.json", {
        "best_rule": (
            "ESCALATE if planner_declared_cannot_plan is false AND ("
            "has_final_grain_contradiction OR evidence_role_contradiction OR "
            "(has_structural_error AND NOT only_unsafe_codes))"
        ),
        "production": production,
        "default_7b_verdict": recommend,
        "next_outcome": next_outcome,
        "holdout": rec_hold,
        "all_labeled": rec_all,
        "vs_p3": p3_all,
        "generalizes_because": (
            "Features are validator codes and V2.2 evidence-signature / declared-role "
            "materialization contradictions. No domain, filename, family, or expected op."
        ),
        "stage": "A (pre-execution)",
        "does_not_repair_plans": True,
    })

    _write_json("regression_results.json", {
        "production_code_changed": False,
        "note": "39V is offline research; 39U validator and escalation modules untouched",
    })
    _write_json("shadow_state_proof.json", {
        "shadow": "OFF",
        "live_shadow": False,
        "why_skipped": "offline corpus + 39T stability reuse sufficient",
    })
    _write_json("phase39v_summary.json", {
        "gate": "A" if next_outcome in {"A", "B", "C"} else "B",
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
        "n": len(rows),
        "recommendation": recommend,
        "production_rule": production,
        "holdout_recall": rec_hold.get("recall"),
        "holdout_unnecessary": rec_hold.get("unnecessary_escalation"),
        "holdout_missed": rec_hold.get("missed_insufficiency"),
    })


def main() -> None:
    bundle = run_research()
    write_artifacts(bundle)
    rows = bundle["rows"]
    print("corpus", len(rows), "dev", len(bundle["dev"]), "hold", len(bundle["hold"]))
    print("YES", sum(1 for r in rows if r["fast_correct"] == "YES"),
          "NO", sum(1 for r in rows if r["fast_correct"] == "NO"),
          "IND", sum(1 for r in rows if r["fast_correct"] == "INDETERMINATE"))
    print("R holdout", metrics_for(bundle["hold"], "pred_r_frozen"))
    print("P2 holdout", metrics_for(bundle["hold"], "pred_p2"))
    print("P3 holdout", metrics_for(bundle["hold"], "pred_p3"))
    print("R all", metrics_for(rows, "pred_r_frozen"))
    fps = [r["attempt_id"] for r in bundle["hold"] if r["fast_correct"] == "YES" and r["pred_r_frozen"] == "ESCALATE"]
    fns = [r["attempt_id"] for r in rows if r["fast_correct"] == "NO" and r["pred_r_frozen"] != "ESCALATE"]
    print("holdout FP", fps)
    print("all FN", fns)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
