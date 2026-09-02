"""Phase 40H freeze tests. No production contract generation or checker wiring."""

from __future__ import annotations

import inspect
import json
from collections import Counter
from pathlib import Path

from core.integrate.semantic_escalation import SEMANTIC_VERIFIER_MODEL, SEMANTIC_VERIFIER_VARIANT
from tests.benchmark_multi.phase40e_design import parse_contract_structural
from tests.benchmark_multi.phase40h_research import (
    PHASE40G_SHA,
    V1_PROMPT,
    build_fresh_holdout,
    check_v1_observer,
    payload_i0,
    reanalyze_40d,
)


def test_40g_sha_and_production_freeze() -> None:
    assert PHASE40G_SHA == "ac819329a3fec3737285f4c4b83d33cd66023ea6"
    assert SEMANTIC_VERIFIER_MODEL == "qwen2.5:7b"
    assert SEMANTIC_VERIFIER_VARIANT == "V1"
    for rel in (
        "core/integrate/integration_planner.py",
        "core/integrate/integration_plan_types.py",
        "core/integrate/integration_plan_validate.py",
        "core/integrate/semantic_escalation.py",
        "core/integrate/semantic_verifier.py",
        "core/integrate/schema_lineage.py",
    ):
        text = Path(rel).read_text()
        assert "PHASE40H" not in text
        assert "SemanticRequirementContract" not in text


def test_fresh_holdout_distribution_and_i0() -> None:
    rows = build_fresh_holdout()
    assert len(rows) >= 30
    ids = [r["attempt_id"] for r in rows]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("h40-") for i in ids)
    dist = Counter(r["fast_correct"] for r in rows)
    n = len(rows)
    assert 0.55 <= dist["YES"] / n <= 0.70
    assert 0.25 <= dist["NO"] / n <= 0.40
    assert dist["IND"] / n <= 0.10
    p = payload_i0(rows[0])
    assert "user_prompt" in p and "schema_inventory" in p
    assert "integration_plan" not in p
    assert "do NOT write an IntegrationPlan" in V1_PROMPT


def test_checker_uses_observer_not_prompt() -> None:
    src = inspect.getsource(check_v1_observer)
    assert "user_prompt" not in src
    assert "semantic_label" not in src
    schemas = {"w.xlsx": ["aisle", "bin", "qty"]}
    raw = {
        "contract_version": "1",
        "grounding_status": "grounded",
        "required_grain": [{
            "role_id": "g1",
            "semantic_label": "ignored",
            "binding": {"source_id": "w.xlsx", "column_ref": "aisle"},
            "grounding_status": "grounded",
            "required_for_answerability": True,
        }],
    }
    parsed = parse_contract_structural(raw, schemas)
    wrong = {
        "status": "planned",
        "final_output": "a",
        "steps": [{
            "op": "aggregate", "inputs": ["w.xlsx"], "output": "a",
            "params": {"group_by": ["bin"], "metrics": [{"column": "qty", "function": "sum", "alias": "qty"}]},
        }],
    }
    chk = check_v1_observer(parsed, plan=wrong, schemas=schemas)
    assert chk["status"] == "CONTRADICTION"
    na = check_v1_observer(
        parsed,
        plan={"status": "cannot_plan", "steps": [], "final_output": None},
        schemas=schemas,
    )
    assert na["status"] == "NOT_APPLICABLE"


def test_empty_gold_overdeclare_is_semantic_not_observer() -> None:
    rec = {
        "fast_correct": "YES",
        "gold_bindings": [],
        "gold_abstain": False,
    }
    packed = {
        "error": None,
        "parsed": {
            "valid": True,
            "grounding_status": "grounded",
            "required_grain": [{
                "role_id": "g1",
                "grounding_status": "grounded",
                "binding": {"source_id": "i.xlsx", "column_ref": "units"},
            }],
        },
        "checker": {"status": "CONTRADICTION"},
    }
    from tests.benchmark_multi.phase40h_research import score_row
    sc = score_row(packed, rec)
    assert sc["OVERDECLARE"] is True
    assert sc["SEMANTIC_FALSE_BLOCK"] is True
    assert sc["OBSERVER_FALSE_BLOCK"] is False


def test_required_artifacts_and_i0() -> None:
    note = reanalyze_40d()
    assert note.get("role") == "OBSERVER_REANALYSIS_ONLY"
    assert note.get("v1_contract_regenerated") is False
    text = Path("tests/benchmark_multi/phase40h_research.py").read_text()
    assert "NO_SAFE_EXISTING_CALL_REUSE" in text
    assert "NO_SAFE_CONTRACT_CALL_TRIGGER" in text
    assert "payload_i1" not in text
    assert "ACTUAL_STRONG_RECOVERY" in text
    assert "PROXY_RECOVERABLE" in text
    assert "OBSERVER_REANALYSIS_ONLY" in text
    out = Path("benchmark_results/multi/phase40h")
    for name in (
        "phase40h_summary.json",
        "strategy_conclusion.json",
        "observer_false_block.json",
        "existing_call_reuse_audit.json",
        "shadow_state_proof.json",
        "strong_recovery_subset.json",
        "useful_contract_detection.json",
        "phase40d_reanalysis.json",
    ):
        assert (out / name).exists()
    summary = json.loads((out / "phase40h_summary.json").read_text())
    assert summary["migration"] == "NOT_APPROVED"
    assert summary["shadow"] == "OFF"
    assert summary["production_change"] == "NO_PRODUCTION_CHANGE"
    assert summary["observer_false_block"] == 0
    assert summary["verdict"] == "NO_SAFE_OPERATIONAL_STRATEGY"
    assert summary["ACTUAL_STRONG_RECOVERY"] == "NOT_MEASURED"
    assert summary["phase40d_43"] == "OBSERVER_REANALYSIS_ONLY"
    recov = json.loads((out / "strong_recovery_subset.json").read_text())
    assert recov["ACTUAL_STRONG_RECOVERY"] == "NOT_MEASURED"
    useful = json.loads((out / "useful_contract_detection.json").read_text())
    assert useful["ACTUAL_STRONG_RECOVERY"] == "NOT_MEASURED"
    assert "lookalike" in useful["basis"]
    d40 = json.loads((out / "phase40d_reanalysis.json").read_text())
    assert d40["role"] == "OBSERVER_REANALYSIS_ONLY"
