"""Phase 39D: schema lineage + verifier materialization grounding tests."""

from __future__ import annotations

import json
from pathlib import Path

from core.integrate.schema_lineage import build_schema_lineage
from core.integrate.semantic_verifier import build_verifier_payload

ROOT = Path(__file__).resolve().parents[1]
FIX_FP = ROOT / "tests/benchmark_multi/fixtures/phase39d/live_fp_finance.json"
FIX_FF = ROOT / "tests/benchmark_multi/fixtures/phase39d/live_ff_energy.json"
FIX_CONS = ROOT / "tests/benchmark_multi/fixtures/phase39d/live_consistency_set.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_t1_aspirational_refs_are_unresolved() -> None:
    fx = _load(FIX_FP)
    ev = build_schema_lineage(fx["plan"], fx["source_schemas"])
    unresolved = {r["column"] for r in ev["unresolved_column_refs"]}
    assert any("." in c for c in unresolved)  # aspirational dotted refs
    assert "actual_spend" not in ev["final_schema"]
    assert "budgeted_spend" not in ev["final_schema"]
    joined = ev["step_outputs"].get("joined_data") or []
    assert "amount_left" in joined and "amount_right" in joined
    assert set(ev["claimed_columns_absent_from_final"]) >= {
        "actual_spend",
        "budgeted_spend",
    }


def test_t2_valid_dual_side_join_materializes_both_metrics() -> None:
    fx = _load(FIX_FF)
    ev = build_schema_lineage(fx["plan"], fx["source_schemas"])
    assert set(ev["final_schema"]) >= {"site_id", "kwh_left", "kwh_right"}
    assert ev["unresolved_column_refs"] == []
    assert ev["claimed_columns_absent_from_final"] == []


def test_t3_collapsed_one_side_absent_from_final() -> None:
    case = next(c for c in _load(FIX_CONS)["cases"] if c["id"] == "AS-03-one-side-collapsed")
    ev = build_schema_lineage(case["plan"], case["source_schemas"])
    assert "total_kwh" in ev["final_schema"]
    assert "kwh_left" not in ev["final_schema"]
    assert "kwh_right" not in ev["final_schema"]


def test_t4_roles_absent_still_materializes_dual_side() -> None:
    case = next(
        c for c in _load(FIX_CONS)["cases"] if c["id"] == "NR-01-roles-absent-valid-dual"
    )
    req = case["plan"].get("final_output_requirements") or {}
    assert not req.get("output_roles")
    ev = build_schema_lineage(case["plan"], case["source_schemas"])
    assert set(ev["final_schema"]) >= {"site_id", "kwh_left", "kwh_right"}


def test_t5_roles_present_but_unmaterialized_do_not_create_columns() -> None:
    case = next(
        c for c in _load(FIX_CONS)["cases"] if c["id"] == "AS-02-role-nonexistent-cols"
    )
    ev = build_schema_lineage(case["plan"], case["source_schemas"])
    assert "baseline_score" in ev["claimed_columns_absent_from_final"]
    assert "experiment_score" in ev["claimed_columns_absent_from_final"]
    assert "baseline_score" not in ev["final_schema"]


def test_t6_union_aggregate_total_materializes() -> None:
    case = next(
        c for c in _load(FIX_CONS)["cases"] if c["id"] == "VC-01-union-aggregate-total"
    )
    ev = build_schema_lineage(case["plan"], case["source_schemas"])
    assert set(ev["final_schema"]) >= {"cost_center", "total_amount"}
    assert ev["unresolved_column_refs"] == []


def test_t7_overall_total_materializes() -> None:
    case = next(c for c in _load(FIX_CONS)["cases"] if c["id"] == "VC-02-overall-total")
    ev = build_schema_lineage(case["plan"], case["source_schemas"])
    assert "overall_total" in ev["final_schema"]


def test_t8_c2_collapse_has_single_metric() -> None:
    case = next(c for c in _load(FIX_CONS)["cases"] if c["id"] == "AS-03-one-side-collapsed")
    ev = build_schema_lineage(case["plan"], case["source_schemas"])
    assert ev["final_schema"] == ["site_id", "total_kwh"]


def test_t10_no_output_roles_payload_backward_compatible() -> None:
    plan = {
        "status": "planned",
        "steps": [
            {
                "id": "u1",
                "op": "union_rows",
                "inputs": ["a.xlsx", "b.xlsx"],
                "output": "final",
                "params": {"column_policy": "aligned"},
            }
        ],
        "final_output": "final",
        "final_output_requirements": {
            "grain": "detail",
            "required_columns": ["entity_id", "value"],
        },
    }
    payload = build_verifier_payload(
        user_prompt="append tables",
        plan=plan,
        variant="V1",
        independent=True,
        materialization_mode="none",
    )
    assert "plan_structure" in payload
    assert "planner_claims" in payload
    assert "materialization_evidence" not in payload


def test_payload_includes_lineage_when_schemas_provided() -> None:
    fx = _load(FIX_FF)
    payload = build_verifier_payload(
        user_prompt=fx["prompt"],
        plan=fx["plan"],
        variant="V1",
        source_schemas=fx["source_schemas"],
        materialization_mode="lineage_claims_separated",
    )
    assert "materialization_evidence" in payload
    assert set(payload["materialization_evidence"]["final_schema"]) >= {
        "site_id",
        "kwh_left",
        "kwh_right",
    }
    assert str(payload.get("planner_claims_authority", "")).startswith("NON_AUTHORITATIVE")


def test_no_domain_keyword_routing_in_schema_lineage() -> None:
    text = Path("core/integrate/schema_lineage.py").read_text(encoding="utf-8").lower()
    for b in ["finance", "energy", "increase", "decrease", "july", "august"]:
        assert b not in text
