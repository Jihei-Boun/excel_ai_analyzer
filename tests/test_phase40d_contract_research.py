"""Phase 40D freeze tests. Production planner/DSL/validator/verifier unchanged."""

from __future__ import annotations

from pathlib import Path

from core.integrate.result_observation import (
    MAX_RESULT_SAMPLE_COLUMNS,
    MAX_RESULT_SAMPLE_ROWS,
    MAX_RESULT_SERIALIZED_CHARS,
)
from core.integrate.semantic_escalation import (
    MAX_SEMANTIC_ESCALATIONS,
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
)
from tests.benchmark_multi.phase40d_research import (
    PHASE40C_SHA,
    PROMPT_SHA,
    build_corpus,
    parse_contract,
    score_declaration,
)


def test_40c_sha_and_production_frozen() -> None:
    assert PHASE40C_SHA == "a1f57c2f6a764b4c39e47e6166a0b8745ffb06e7"
    assert SEMANTIC_VERIFIER_MODEL == "qwen2.5:7b"
    assert SEMANTIC_VERIFIER_VARIANT == "V1"
    assert MAX_RESULT_SAMPLE_ROWS == 5
    assert MAX_RESULT_SAMPLE_COLUMNS == 24
    assert MAX_RESULT_SERIALIZED_CHARS == 4000
    assert MAX_SEMANTIC_ESCALATIONS == 1
    planner = Path("core/integrate/integration_planner.py").read_text()
    types = Path("core/integrate/integration_plan_types.py").read_text()
    val = Path("core/integrate/integration_plan_validate.py").read_text()
    esc = Path("core/integrate/semantic_escalation.py").read_text()
    ver = Path("core/integrate/semantic_verifier.py").read_text()
    for text in (planner, types, val, esc, ver):
        assert "PHASE40D" not in text
        assert "semantic_requirements" not in text
    assert "observe_result_for_verifier" in esc


def test_corpus_and_parser_no_autocomplete() -> None:
    rows = build_corpus()
    ids = [r["attempt_id"] for r in rows]
    assert len(ids) >= 36
    assert len(ids) == len(set(ids))
    yes = sum(r["fast_correct"] == "YES" for r in rows)
    no = sum(r["fast_correct"] == "NO" for r in rows)
    assert 0.55 <= yes / len(rows) <= 0.70
    assert 0.25 <= no / len(rows) <= 0.40
    assert any(r.get("gold_abstain") for r in rows)
    assert any(r.get("origin") == "phase40d_new" for r in rows)
    assert any(r.get("origin") == "historical_m2" for r in rows)
    dev = sum(r.get("split") == "DEV" for r in rows)
    assert 0.50 <= dev / len(rows) <= 0.70
    parsed = parse_contract({"grounding_status": "nope"}, {"f.xlsx": ["a"]})
    assert parsed["grounding_status"] == "cannot_ground"
    assert parsed["required_grain"] == []
    assert parsed["required_outputs"] == []
    assert len(PROMPT_SHA) == 64
    filled = parse_contract({}, {"f.xlsx": ["campus"]})
    assert filled["required_grain"] == []


def test_metric_taxonomy_splits_observation_and_unusable() -> None:
    rec_no = {"fast_correct": "NO", "gold_grain_columns": ["campus"], "gold_must_not_bind": []}
    timeout = score_declaration(
        {
            "error": "ReadTimeout",
            "contract": {
                "parse_ok": True,
                "cannot_determine": False,
                "grounding_status": "cannot_ground",
                "required_grain": [],
                "required_outputs": [],
                "required_distinctions": [],
                "parser_notes": ["invalid_grounding_status"],
            },
            "checker": {"status": "consistent", "findings": []},
        },
        rec_no,
    )
    assert timeout["CONTRACT_USABLE"] is False
    assert timeout["UNUSABLE_REASON"] == "OPERATIONAL_ERROR"
    assert timeout["SELF_JUSTIFYING_CONTRACT"] is False

    empty = score_declaration(
        {
            "error": None,
            "contract": {
                "parse_ok": True,
                "cannot_determine": False,
                "grounding_status": "cannot_ground",
                "required_grain": [],
                "required_outputs": [],
                "required_distinctions": [],
                "parser_notes": [],
            },
            "checker": {"status": "consistent", "findings": []},
        },
        rec_no,
    )
    assert empty["CONTRACT_USABLE"] is False
    assert empty["UNUSABLE_REASON"] == "EMPTY_UNUSABLE_CONTRACT"
    assert empty["SELF_JUSTIFYING_CONTRACT"] is False

    omitted = score_declaration(
        {
            "error": None,
            "contract": {
                "parse_ok": True,
                "cannot_determine": False,
                "grounding_status": "partially_grounded",
                "required_grain": [{"role_id": "1", "semantic_role": "x", "binding": None}],
                "required_outputs": [],
                "required_distinctions": [],
                "parser_notes": [],
            },
            "checker": {"status": "consistent", "findings": [{"rule": "K1_grain", "status": "indeterminate"}]},
        },
        rec_no,
    )
    assert omitted["CONTRACT_USABLE"] is True
    assert omitted["SELF_JUSTIFYING_CONTRACT"] is True

    rec_yes = {"fast_correct": "YES", "gold_grain_columns": ["id"], "gold_output_columns": ["v"]}
    gap = score_declaration(
        {
            "error": None,
            "contract": {
                "parse_ok": True,
                "cannot_determine": False,
                "grounding_status": "grounded",
                "required_grain": [{
                    "role_id": "1",
                    "semantic_role": "id",
                    "binding": {"source": "a.xlsx", "column": "id", "hallucinated": False},
                }],
                "required_outputs": [{
                    "role_id": "2",
                    "semantic_role": "v",
                    "binding": {"source": "a.xlsx", "column": "v", "hallucinated": False},
                }],
                "required_distinctions": [],
                "parser_notes": [],
            },
            "checker": {"status": "contradiction", "findings": [{"rule": "K2_output", "status": "contradiction"}]},
            "observation": {"status": "planned", "ops": ["rename_columns", "join"], "final_schema": ["id", "v_l"]},
        },
        rec_yes,
    )
    assert gap["CONTRACT_FALSE_BLOCK"] is True
    assert gap["FALSE_BLOCK_CAUSE"] == "PLAN_OBSERVATION_GAP"
    assert gap["FALSE_BLOCK_SEMANTIC"] is False
    assert gap["FALSE_BLOCK_OBSERVATION_GAP"] is True
