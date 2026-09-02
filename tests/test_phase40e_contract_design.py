"""Phase 40E design-spec tests. Production planner/DSL/validator/verifier unchanged."""

from __future__ import annotations

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
from tests.benchmark_multi.phase40e_design import (
    CHECK_OUTCOMES,
    PHASE40D_SHA,
    REMOVED_FROM_40D,
    check_plan_contract,
    observe_required_ungrounded,
    parse_contract_structural,
    rename_demo_schemas,
    rename_then_select_plan,
    valid_example_cannot_ground,
    valid_example_grounded,
    wrong_group_plan,
)


def test_40d_sha_and_production_frozen() -> None:
    assert PHASE40D_SHA == "b1382d0fad1656aa0e5328885cede2a73b060620"
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
    ):
        text = Path(rel).read_text()
        assert "PHASE40E" not in text
        assert "SemanticRequirementContract" not in text


def test_v1_removes_broad_ontology() -> None:
    for name in REMOVED_FROM_40D:
        assert name in {
            "partially_grounded",
            "cannot_determine",
            "required_outputs",
            "function",
            "required_distinctions",
            "required_relations",
        }


def test_parser_drops_label_and_rejects_partial() -> None:
    schemas = rename_demo_schemas()
    ok = parse_contract_structural(valid_example_grounded(), schemas)
    assert ok["valid"] is True
    assert "semantic_label" not in ok["required_grain"][0]
    bad = dict(valid_example_grounded())
    bad["grounding_status"] = "partially_grounded"
    parsed = parse_contract_structural(bad, schemas)
    assert parsed["valid"] is False
    empty = parse_contract_structural(
        {"contract_version": "1", "grounding_status": "grounded", "required_grain": []},
        schemas,
    )
    assert empty["valid"] is False


def test_rename_uses_origins_not_display_name() -> None:
    schemas = rename_demo_schemas()
    plan = rename_then_select_plan()
    lin = build_schema_lineage(plan, schemas)
    assert "brightness" in lin["final_schema"]
    assert "entity_key" not in lin["final_schema"]
    origins = lin["final_column_origins"]["id_out"]
    assert {"source": "src_a", "column": "entity_key"} in origins
    parsed = parse_contract_structural(valid_example_grounded(), schemas)
    out = check_plan_contract(parsed, plan=plan, schemas=schemas)
    assert out["status"] == "SATISFIED"


def test_wrong_group_is_contradiction() -> None:
    schemas = rename_demo_schemas()
    parsed = parse_contract_structural(valid_example_grounded(), schemas)
    out = check_plan_contract(parsed, plan=wrong_group_plan(), schemas=schemas)
    assert out["status"] == "CONTRADICTION"


def test_cannot_plan_is_not_empty_schema_contradiction() -> None:
    schemas = rename_demo_schemas()
    parsed = parse_contract_structural(valid_example_cannot_ground(), schemas)
    out = check_plan_contract(parsed, plan={"status": "cannot_plan", "steps": [], "final_output": None}, schemas=schemas)
    assert out["status"] == "NOT_APPLICABLE"


def test_timeout_is_operational_not_semantic() -> None:
    parsed = parse_contract_structural(valid_example_grounded(), rename_demo_schemas())
    out = check_plan_contract(parsed, plan=None, schemas={}, generation_error="ReadTimeout")
    assert out["status"] == "OPERATIONAL_FAILURE"
    assert out["status"] in CHECK_OUTCOMES


def test_no_prompt_in_checker_function() -> None:
    src = Path("tests/benchmark_multi/phase40e_design.py").read_text()
    assert "user_prompt" not in src.split("def check_plan_contract")[1].split("def rename_demo")[0]


def test_answerability_is_fact_not_pipeline_policy() -> None:
    schemas = rename_demo_schemas()
    parsed = parse_contract_structural(valid_example_cannot_ground(), schemas)
    facts = observe_required_ungrounded(parsed)
    assert facts == [{
        "role_id": "g1",
        "fact": "REQUIRED_OBLIGATION_UNGROUNDED",
        "pipeline_action": None,
    }]
    out = check_plan_contract(
        parsed,
        plan={"status": "cannot_plan", "steps": [], "final_output": None},
        schemas=schemas,
    )
    assert out["status"] == "NOT_APPLICABLE"
    assert out["status"] != "cannot_plan"
    assert all(f.get("pipeline_action") is None for f in out["answerability_facts"])
    src = Path("tests/benchmark_multi/phase40e_design.py").read_text()
    assert "REQUIRED_OBLIGATION_UNGROUNDED" in src
    assert "python_must_not_decide" in src
    assert "OUT_OF_SCOPE_FOR_IMPLEMENTATION" in src


def test_immutability_and_versioned_reresolution_boundary() -> None:
    src = Path("tests/benchmark_multi/phase40e_design.py").read_text()
    assert "immutable_within_same_semantic_evidence_snapshot" in src
    assert "planner_failure_does_not_rewrite" in src
    assert "re_resolution_implemented_in_40e" in src
    assert "new immutable artifact" in src
    note = Path("docs/learning_note/phase40e_minimal_semantic_grain_role_contract_design.md").read_text()
    assert "REQUIRED_OBLIGATION_UNGROUNDED" in note
    assert "같은 semantic-evidence snapshot" in note
    assert "re-resolution은 구현하지 않는다" in note
