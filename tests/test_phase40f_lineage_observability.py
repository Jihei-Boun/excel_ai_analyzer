"""Phase 40F freeze tests. Production planner/DSL/validator/verifier unchanged."""

from __future__ import annotations

import inspect
from collections import Counter
from pathlib import Path

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
from tests.benchmark_multi.phase40e_design import parse_contract_structural
from tests.benchmark_multi.phase40f_research import (
    PHASE40E_SHA,
    SA,
    build_fixtures,
    check_declared_grain,
    evaluate,
    observe_final_grain,
    write_artifacts,
)


def test_40e_sha_and_production_frozen() -> None:
    assert PHASE40E_SHA == "056ca4cb072c8dbf6534afc0d1bd68eb0631212a"
    assert SEMANTIC_VERIFIER_MODEL == "qwen2.5:7b"
    assert SEMANTIC_VERIFIER_VARIANT == "V1"
    assert MAX_RESULT_SAMPLE_ROWS == 5
    assert MAX_RESULT_SAMPLE_COLUMNS == 24
    assert MAX_RESULT_SERIALIZED_CHARS == 4000
    assert MAX_SEMANTIC_ESCALATIONS == 1
    for rel in (
        "core/integrate/integration_planner.py",
        "core/integrate/integration_plan_types.py",
        "core/integrate/integration_plan_validate.py",
        "core/integrate/semantic_escalation.py",
        "core/integrate/semantic_verifier.py",
        "core/integrate/result_observation.py",
        "core/integrate/schema_lineage.py",
    ):
        text = Path(rel).read_text()
        assert "PHASE40F" not in text
        assert "SemanticRequirementContract" not in text
        assert "check_declared_grain" not in text


def test_checker_never_reads_meaning() -> None:
    src = inspect.getsource(check_declared_grain) + inspect.getsource(observe_final_grain)
    for forbidden in (
        "user_prompt",
        "semantic_label",
        "difflib",
        "SequenceMatcher",
        "fuzzy",
        "benchmark_family",
        "gold_grain",
    ):
        assert forbidden not in src


def test_corpus_valid_heavy_and_coverage() -> None:
    fixtures = build_fixtures()
    assert len(fixtures) >= 50
    ids = [f["fixture_id"] for f in fixtures]
    assert len(ids) == len(set(ids))
    oracle = Counter(f["oracle"] for f in fixtures)
    assert oracle["PRESERVED"] / len(fixtures) >= 0.5
    families = {f["family"] for f in fixtures}
    for need in {
        "rename", "aggregate", "join", "union", "branch", "historical",
        "cannot_plan", "invalid", "replay_40d", "immutability",
    }:
        assert need in families
    notes = " ".join(f["note"] for f in fixtures)
    assert "A→B→C" in notes or any(f["fixture_id"] == "p-ren2" for f in fixtures)
    assert any(f["fixture_id"] == "p-ren3" for f in fixtures)
    assert any(f["fixture_id"] == "c-agg-metric" for f in fixtures)
    assert any(f["fixture_id"] == "c-m2-tid" for f in fixtures)
    assert any(f["fixture_id"] == "p-m2-lookalike" for f in fixtures)
    assert all(f.get("request_id") and f.get("attempt_id") and f.get("semantic_contract_id") for f in fixtures)


def test_rename_ancestry_not_display_name() -> None:
    plan = {
        "status": "planned",
        "final_output": "s",
        "steps": [
            {
                "id": "r1", "op": "rename_columns", "inputs": ["src_a"], "output": "r",
                "params": {"mapping": {"entity_key": "id_out", "measure": "brightness"}},
            },
            {
                "id": "s1", "op": "select_columns", "inputs": ["r"], "output": "s",
                "params": {"columns": ["id_out", "brightness"]},
            },
        ],
    }
    contract = {
        "contract_version": "1",
        "grounding_status": "grounded",
        "required_grain": [{
            "role_id": "g1",
            "semantic_label": "must be ignored by python",
            "binding": {"source_id": "src_a", "column_ref": "entity_key"},
            "grounding_status": "grounded",
            "required_for_answerability": True,
        }],
    }
    chk = check_declared_grain(contract, plan=plan, schemas=SA)
    assert chk["status"] == "PRESERVED"
    lin = build_schema_lineage(plan, SA)
    assert "entity_key" not in (lin.get("final_schema") or [])
    assert lin["final_column_origins"]["id_out"] == [{"source": "src_a", "column": "entity_key"}]


def test_aggregate_collapse_is_not_lineage_presence() -> None:
    plan = {
        "status": "planned",
        "final_output": "a",
        "steps": [{
            "id": "a1", "op": "aggregate", "inputs": ["src_a"], "output": "a",
            "params": {
                "group_by": ["extra"],
                "metrics": [{"column": "entity_key", "function": "count", "alias": "n_keys"}],
            },
        }],
    }
    contract = {
        "contract_version": "1",
        "grounding_status": "grounded",
        "required_grain": [{
            "role_id": "g1",
            "semantic_label": "ignored",
            "binding": {"source_id": "src_a", "column_ref": "entity_key"},
            "grounding_status": "grounded",
            "required_for_answerability": True,
        }],
    }
    chk = check_declared_grain(contract, plan=plan, schemas=SA)
    assert chk["status"] == "CONTRADICTION"
    lin = build_schema_lineage(plan, SA)
    assert "n_keys" in (lin.get("final_schema") or [])


def test_cannot_plan_and_cannot_ground_are_na() -> None:
    contract = {
        "contract_version": "1",
        "grounding_status": "grounded",
        "required_grain": [{
            "role_id": "g1",
            "semantic_label": "x",
            "binding": {"source_id": "src_a", "column_ref": "entity_key"},
            "grounding_status": "grounded",
            "required_for_answerability": True,
        }],
    }
    chk = check_declared_grain(
        contract,
        plan={"status": "cannot_plan", "steps": [], "final_output": None},
        schemas=SA,
    )
    assert chk["status"] == "NOT_APPLICABLE"
    ungrounded = {
        "contract_version": "1",
        "grounding_status": "cannot_ground",
        "required_grain": [{
            "role_id": "g1",
            "semantic_label": "x",
            "binding": None,
            "grounding_status": "cannot_ground",
            "required_for_answerability": True,
        }],
    }
    chk2 = check_declared_grain(ungrounded, plan={"status": "planned", "final_output": "src_a", "steps": []}, schemas=SA)
    assert chk2["status"] == "NOT_APPLICABLE"
    assert chk2["answerability_facts"][0]["fact"] == "REQUIRED_OBLIGATION_UNGROUNDED"
    assert chk2["answerability_facts"][0]["pipeline_action"] is None


def test_no_same_name_fallback() -> None:
    contract = {
        "contract_version": "1",
        "grounding_status": "grounded",
        "required_grain": [{
            "role_id": "g1",
            "semantic_label": "x",
            "binding": {"source_id": "src_a", "column_ref": "entity_key"},
            "grounding_status": "grounded",
            "required_for_answerability": True,
        }],
    }
    chk = check_declared_grain(
        contract,
        plan={"status": "planned", "final_output": "s", "steps": []},
        schemas={"src_b": ["entity_key", "val_b"]},
    )
    assert chk["status"] == "INVALID_CONTRACT"
    assert chk["gap"] == "SOURCE_NOT_FOUND"


def test_parser_drops_label() -> None:
    raw = {
        "contract_version": "1",
        "grounding_status": "grounded",
        "required_grain": [{
            "role_id": "g1",
            "semantic_label": "campus entity",
            "binding": {"source_id": "src_a", "column_ref": "entity_key"},
            "grounding_status": "grounded",
            "required_for_answerability": True,
        }],
    }
    parsed = parse_contract_structural(raw, SA)
    assert parsed["valid"] is True
    assert "semantic_label" not in parsed["required_grain"][0]


def test_safety_metrics_and_artifacts() -> None:
    fixtures = build_fixtures()
    results = evaluate(fixtures)
    write_artifacts(fixtures, results)
    assert sum(r["FALSE_CONTRADICTION"] for r in results) == 0
    assert sum(r["FALSE_PRESERVED"] for r in results) == 0
    assert all(r["agree"] for r in results)
    replay = [r for r in results if r["family"] == "replay_40d"]
    by_id = {r["fixture_id"]: r for r in replay}
    assert by_id["r40d-rename"]["checker"] == "PRESERVED"
    assert by_id["r40d-sides"]["checker"] == "PRESERVED"
    assert by_id["r40d-cannot-plan"]["checker"] == "NOT_APPLICABLE"
    assert by_id["r40d-compare-tod"]["checker"] == "INDETERMINATE"
    m2 = {r["fixture_id"]: r for r in results}
    assert m2["c-m2-tid"]["checker"] == "CONTRADICTION"
    assert m2["p-m2-lookalike"]["checker"] == "PRESERVED"
    imm = [f for f in fixtures if f["family"] == "immutability"]
    assert imm[0]["semantic_contract_id"] == imm[1]["semantic_contract_id"]
    assert imm[0]["contract"] == imm[1]["contract"]
    assert imm[0]["attempt_id"] != imm[1]["attempt_id"]
    out = Path("benchmark_results/multi/phase40f")
    for name in (
        "baseline_freeze.json",
        "contract_fixture_registry.json",
        "manual_structural_oracle.json",
        "observation_source_inventory.json",
        "v22_sufficiency_audit.json",
        "rename_matrix.json",
        "aggregate_matrix.json",
        "join_matrix.json",
        "union_matrix.json",
        "branch_state_matrix.json",
        "multi_stage_stress.json",
        "alias_depth_stress.json",
        "cannot_plan_cases.json",
        "invalid_binding_cases.json",
        "observation_gap_taxonomy.json",
        "checker_pseudocode.json",
        "checker_results.json",
        "false_contradiction_review.json",
        "false_preserved_review.json",
        "indeterminate_review.json",
        "phase40d_gap_replay.json",
        "request_attempt_isolation.json",
        "performance_results.json",
        "observation_extension_proposal.json",
        "future_implementation_preconditions.json",
        "regression_results.json",
        "shadow_state_proof.json",
        "phase40f_summary.json",
    ):
        assert (out / name).is_file(), name
