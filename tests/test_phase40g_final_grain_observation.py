"""Phase 40G freeze tests. Observer only; no contract checker wiring."""

from __future__ import annotations

import inspect
from pathlib import Path

from core.integrate.result_observation import (
    MAX_RESULT_SAMPLE_COLUMNS,
    MAX_RESULT_SAMPLE_ROWS,
    MAX_RESULT_SERIALIZED_CHARS,
)
from core.integrate.schema_lineage import (
    GRAIN_INDETERMINATE,
    GRAIN_KNOWN,
    GRAIN_NOT_APPLICABLE,
    build_schema_lineage,
    observe_final_grain_identities,
)
from core.integrate.semantic_escalation import (
    MAX_SEMANTIC_ESCALATIONS,
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
)
from tests.benchmark_multi.phase40f_research import build_fixtures
from tests.benchmark_multi.phase40g_research import (
    PHASE40F_SHA,
    evaluate,
    manual_grain_oracle,
)


def test_40f_sha_and_production_freeze() -> None:
    assert PHASE40F_SHA == "561138432aca0e6a88e5d33eaf1063bc4f76bac5"
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
        "core/integrate/relational_state.py",
        "core/integrate/integration_execute.py",
    ):
        text = Path(rel).read_text()
        assert "PHASE40G" not in text
        assert "observe_final_grain_identities" not in text
        assert "SemanticRequirementContract" not in text


def test_observer_never_reads_meaning() -> None:
    src = inspect.getsource(observe_final_grain_identities)
    for forbidden in (
        "user_prompt",
        "semantic_label",
        "required_grain",
        "SemanticRequirementContract",
        "difflib",
        "fuzzy",
        "benchmark_family",
    ):
        assert forbidden not in src


def test_cannot_plan_not_empty_known() -> None:
    obs = observe_final_grain_identities(
        {"status": "cannot_plan", "steps": [], "final_output": None},
        {"src_a": ["entity_key"]},
    )
    assert obs["status"] == GRAIN_NOT_APPLICABLE
    assert obs["identities"] == []
    assert obs["reason"] == "cannot_plan"


def test_source_without_unique_identity_is_indeterminate() -> None:
    obs = observe_final_grain_identities(
        {
            "status": "planned",
            "final_output": "s",
            "steps": [{
                "id": "s1", "op": "select_columns", "inputs": ["src_a"], "output": "s",
                "params": {"columns": ["entity_key", "measure"]},
            }],
        },
        {"src_a": ["entity_key", "measure"]},
    )
    assert obs["status"] == GRAIN_INDETERMINATE
    assert obs["identities"] == []


def test_aggregate_sets_canonical_grain_through_rename() -> None:
    schemas = {"src_a": ["entity_key", "measure"]}
    plan = {
        "status": "planned",
        "final_output": "r",
        "steps": [
            {
                "id": "a1", "op": "aggregate", "inputs": ["src_a"], "output": "a",
                "params": {
                    "group_by": ["entity_key"],
                    "metrics": [{"column": "measure", "function": "sum", "alias": "measure"}],
                },
            },
            {
                "id": "r1", "op": "rename_columns", "inputs": ["a"], "output": "r",
                "params": {"mapping": {"entity_key": "id_out"}},
            },
        ],
    }
    obs = observe_final_grain_identities(plan, schemas)
    assert obs["status"] == GRAIN_KNOWN
    assert obs["identities"] == [{"source_id": "src_a", "origin_column_ref": "entity_key"}]
    lin = build_schema_lineage(plan, schemas)
    assert "entity_key" not in (lin.get("final_schema") or [])


def test_global_aggregate_known_empty_not_indeterminate() -> None:
    obs = observe_final_grain_identities(
        {
            "status": "planned",
            "final_output": "a",
            "steps": [{
                "id": "a1", "op": "aggregate", "inputs": ["src_a"], "output": "a",
                "params": {
                    "group_by": [],
                    "metrics": [{"column": "measure", "function": "sum", "alias": "measure"}],
                },
            }],
        },
        {"src_a": ["entity_key", "measure"]},
    )
    assert obs["status"] == GRAIN_KNOWN
    assert obs["identities"] == []
    assert obs["reason"] == "global_aggregate"


def test_filter_preserves_aggregate_grain() -> None:
    obs = observe_final_grain_identities(
        {
            "status": "planned",
            "final_output": "f",
            "steps": [
                {
                    "id": "a1", "op": "aggregate", "inputs": ["src_a"], "output": "a",
                    "params": {
                        "group_by": ["entity_key"],
                        "metrics": [{"column": "measure", "function": "sum", "alias": "measure"}],
                    },
                },
                {
                    "id": "f1", "op": "filter_rows", "inputs": ["a"], "output": "f",
                    "params": {"conditions": [{"column": "measure", "op": "gt", "value": 0}]},
                },
            ],
        },
        {"src_a": ["entity_key", "measure"]},
    )
    assert obs["status"] == GRAIN_KNOWN
    assert obs["identities"][0]["origin_column_ref"] == "entity_key"


def test_select_dropping_grain_is_indeterminate() -> None:
    obs = observe_final_grain_identities(
        {
            "status": "planned",
            "final_output": "s",
            "steps": [
                {
                    "id": "a1", "op": "aggregate", "inputs": ["src_a"], "output": "a",
                    "params": {
                        "group_by": ["entity_key"],
                        "metrics": [{"column": "measure", "function": "sum", "alias": "measure"}],
                    },
                },
                {
                    "id": "s1", "op": "select_columns", "inputs": ["a"], "output": "s",
                    "params": {"columns": ["measure"]},
                },
            ],
        },
        {"src_a": ["entity_key", "measure"]},
    )
    assert obs["status"] == GRAIN_INDETERMINATE
    assert obs["reason"] == "grain_column_projected_away"


def test_same_source_branch_aggregates_then_join() -> None:
    obs = observe_final_grain_identities(
        {
            "status": "planned",
            "final_output": "j",
            "steps": [
                {
                    "id": "f1", "op": "filter_rows", "inputs": ["src_a"], "output": "f1",
                    "params": {"conditions": [{"column": "extra", "op": "eq", "value": "x"}]},
                },
                {
                    "id": "f2", "op": "filter_rows", "inputs": ["src_a"], "output": "f2",
                    "params": {"conditions": [{"column": "extra", "op": "eq", "value": "y"}]},
                },
                {
                    "id": "a1", "op": "aggregate", "inputs": ["f1"], "output": "g1",
                    "params": {
                        "group_by": ["entity_key"],
                        "metrics": [{"column": "measure", "function": "sum", "alias": "m1"}],
                    },
                },
                {
                    "id": "a2", "op": "aggregate", "inputs": ["f2"], "output": "g2",
                    "params": {
                        "group_by": ["entity_key"],
                        "metrics": [{"column": "measure", "function": "sum", "alias": "m2"}],
                    },
                },
                {
                    "id": "j1", "op": "join", "inputs": ["g1", "g2"], "output": "j",
                    "params": {"left_on": ["entity_key"], "right_on": ["entity_key"]},
                },
            ],
        },
        {"src_a": ["entity_key", "measure", "extra"]},
    )
    assert obs["status"] == GRAIN_KNOWN
    assert obs["identities"] == [{"source_id": "src_a", "origin_column_ref": "entity_key"}]


def test_join_after_aggregate_with_ungrained_side_stays_indeterminate() -> None:
    obs = observe_final_grain_identities(
        {
            "status": "planned",
            "final_output": "j",
            "steps": [
                {
                    "id": "a1", "op": "aggregate", "inputs": ["src_a"], "output": "a",
                    "params": {
                        "group_by": ["entity_key"],
                        "metrics": [{"column": "measure", "function": "sum", "alias": "measure"}],
                    },
                },
                {
                    "id": "j1", "op": "join", "inputs": ["a", "src_b"], "output": "j",
                    "params": {"left_on": ["entity_key"], "right_on": ["other_key"]},
                },
            ],
        },
        {"src_a": ["entity_key", "measure"], "src_b": ["other_key", "val_b"]},
    )
    assert obs["status"] == GRAIN_INDETERMINATE
    assert obs["reason"] == "join_grain_unprovable"


def test_union_same_aggregate_grain() -> None:
    schemas = {"src_a": ["entity_key", "measure"]}
    plan = {
        "status": "planned",
        "final_output": "u",
        "steps": [
            {
                "id": "a1", "op": "aggregate", "inputs": ["src_a"], "output": "g1",
                "params": {
                    "group_by": ["entity_key"],
                    "metrics": [{"column": "measure", "function": "sum", "alias": "measure"}],
                },
            },
            {
                "id": "a2", "op": "aggregate", "inputs": ["src_a"], "output": "g2",
                "params": {
                    "group_by": ["entity_key"],
                    "metrics": [{"column": "measure", "function": "sum", "alias": "measure"}],
                },
            },
            {"id": "u1", "op": "union_rows", "inputs": ["g1", "g2"], "output": "u", "params": {}},
        ],
    }
    obs = observe_final_grain_identities(plan, schemas)
    assert obs["status"] == GRAIN_KNOWN
    assert obs["identities"][0]["origin_column_ref"] == "entity_key"


def test_lineage_payload_does_not_gain_grain_field() -> None:
    ev = build_schema_lineage(
        {
            "status": "planned",
            "final_output": "a",
            "steps": [{
                "id": "a1", "op": "aggregate", "inputs": ["src_a"], "output": "a",
                "params": {
                    "group_by": ["entity_key"],
                    "metrics": [{"column": "measure", "function": "sum", "alias": "m"}],
                },
            }],
        },
        {"src_a": ["entity_key", "measure"]},
    )
    assert "final_grain" not in ev
    assert "observe_final_grain_identities" not in ev


def test_frozen_40f_corpus_safety() -> None:
    fixtures = build_fixtures()
    assert len(fixtures) == 78
    stats = evaluate()
    assert stats["FALSE_KNOWN_GRAIN"] == 0
    assert stats["KNOWN_IDENTITY_MISMATCH"] == 0
    assert stats["MISSED_KNOWN_GRAIN"] == 0
    for fx in fixtures:
        oracle = manual_grain_oracle(fx["fixture_id"])
        obs = observe_final_grain_identities(fx["plan"], fx["schemas"])
        assert obs["status"] == oracle["status"], fx["fixture_id"]
        if oracle["status"] == GRAIN_KNOWN:
            assert [(x["source_id"], x["origin_column_ref"]) for x in obs["identities"]] == [
                (x["source_id"], x["origin_column_ref"]) for x in oracle["identities"]
            ]
    out = Path("benchmark_results/multi/phase40g")
    for name in (
        "baseline_freeze.json",
        "observer_design.json",
        "observer_api.json",
        "operation_semantics.json",
        "phase40f_replay.json",
        "final_grain_unknown_replay.json",
        "rename_replay.json",
        "aggregate_replay.json",
        "join_replay.json",
        "union_replay.json",
        "branch_replay.json",
        "multi_stage_replay.json",
        "false_known_grain_review.json",
        "known_identity_mismatch_review.json",
        "missed_known_grain_review.json",
        "coverage_metrics.json",
        "indeterminate_analysis.json",
        "performance_results.json",
        "regression_results.json",
        "production_diff_proof.json",
        "shadow_state_proof.json",
        "phase40g_summary.json",
    ):
        assert (out / name).is_file(), name
