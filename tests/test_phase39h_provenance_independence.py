"""Phase 39H unit tests: evidence signatures separate fake vs genuine dual-side."""

from __future__ import annotations

import json
from pathlib import Path

from core.integrate.schema_lineage import build_schema_lineage
from core.integrate.semantic_verifier import build_verifier_payload

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests/benchmark_multi/fixtures/phase39h"


def _load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_p39g11_equivalent_signatures():
    fx = _load("p39g11_canonical.json")
    ev = build_schema_lineage(fx["plan"], fx["source_schemas"])
    groups = ev["equivalent_evidence_signature_groups"]
    assert groups
    cols = set()
    for g in groups:
        cols.update(g["final_columns"])
    assert "total_kwh_w1" in cols and "total_kwh_w2" in cols


def test_fake_dual_fd1_equivalent():
    c = next(x for x in _load("fake_dual_family.json")["cases"] if x["id"] == "FD1")
    ev = build_schema_lineage(c["plan"], c["source_schemas"])
    grouped = {
        tuple(sorted(g["final_columns"]))
        for g in ev["equivalent_evidence_signature_groups"]
    }
    assert ("side_a", "side_b") in grouped


def test_genuine_gs1_not_equivalent():
    c = next(
        x for x in _load("genuine_same_origin_dual.json")["cases"] if x["id"] == "GS1"
    )
    ev = build_schema_lineage(c["plan"], c["source_schemas"])
    sigs = ev["final_column_evidence_signatures"]
    assert (
        sigs["side_a"]["row_population"]["filters"]
        != sigs["side_b"]["row_population"]["filters"]
    )
    for g in ev["equivalent_evidence_signature_groups"]:
        assert not ({"side_a", "side_b"} <= set(g["final_columns"]))


def test_v22_payload_exposes_signatures_not_pass_fail():
    fx = _load("p39g11_canonical.json")
    payload = build_verifier_payload(
        user_prompt=fx["prompt"],
        plan=fx["plan"],
        variant="V1",
        independent=True,
        source_schemas=fx["source_schemas"],
        materialization_mode="final_schema_expr_partition",
    )
    me = payload["materialization_evidence"]
    assert "final_column_evidence_signatures" in me
    assert "equivalent_evidence_signature_groups" in me
    assert "identical_evidence_signature_column_sets" in me
    assert "verdict" not in me
    assert "pass" not in me
    assert "fail" not in me


def test_same_origin_is_not_blind_invalid():
    """F1 guard: shared origin alone must not auto-mark invalid in Python."""
    c = next(
        x for x in _load("genuine_same_origin_dual.json")["cases"] if x["id"] == "GS1"
    )
    ev = build_schema_lineage(c["plan"], c["source_schemas"])
    oa = {o["source"] for o in ev["final_column_origins"]["side_a"]}
    ob = {o["source"] for o in ev["final_column_origins"]["side_b"]}
    assert oa == ob == {"data.xlsx"}
    assert "invalid" not in json.dumps(ev).lower()
