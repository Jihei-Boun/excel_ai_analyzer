"""Phase 33: offline semantic verifier tests (no production wiring)."""

from __future__ import annotations

import json

from core.integrate.semantic_verifier import (
    VERIFIER_SYSTEM_PROMPT,
    assert_no_golden_leakage,
    build_verifier_payload,
    run_semantic_verification,
)


def test_structured_pass_fail_uncertain() -> None:
    responses = [
        {"verdict": "pass", "reason_code": "satisfied", "evidence": ["ok"]},
        {"verdict": "fail", "reason_code": "wrong_output_grain", "evidence": ["grain"]},
        {
            "verdict": "uncertain",
            "reason_code": "insufficient_evidence",
            "evidence": ["need more"],
        },
    ]

    def fake(prompt, **kwargs):  # noqa: ANN001
        del prompt, kwargs
        return responses.pop(0)

    plan = {
        "status": "planned",
        "steps": [{"id": "1", "op": "join", "inputs": ["a", "b"], "output": "j", "params": {}}],
        "final_output": "j",
        "final_output_requirements": {"grain": "detail", "required_columns": ["id"]},
    }
    for expected in ("pass", "fail", "uncertain"):
        r = run_semantic_verification(
            user_prompt="connect files",
            plan=plan,
            variant="V1",
            chat_json_fn=fake,
        )
        assert r.verdict == expected
        assert r.parse_ok


def test_malformed_response_handling() -> None:
    def fake(prompt, **kwargs):  # noqa: ANN001
        del prompt, kwargs
        return {"verdict": "maybe", "reason_code": "x"}

    r = run_semantic_verification(
        user_prompt="x",
        plan={"status": "planned", "steps": [], "final_output": None},
        variant="V1",
        chat_json_fn=fake,
    )
    assert r.verdict == "parse_failed"
    assert r.parse_ok is False


def test_no_golden_fields_in_payload() -> None:
    payload = build_verifier_payload(
        user_prompt="sum by category",
        plan={
            "status": "planned",
            "steps": [],
            "final_output": "o",
            "final_output_requirements": {
                "grain": "group",
                "required_columns": ["category", "total"],
            },
        },
        result={"columns": ["category", "total"], "row_count": 3, "sample_rows": []},
        understanding={"file_profiles": [], "relationships": []},
        variant="V3",
    )
    assert_no_golden_leakage(payload)
    blob = json.dumps(payload)
    for banned in (
        "overall_ok",
        "expected_grain",
        "golden",
        "scenario",
        "case_id",
        "failure_categories",
    ):
        assert banned not in blob


def test_verifier_does_not_mutate_plan() -> None:
    plan = {
        "status": "planned",
        "steps": [{"id": "1", "op": "union_rows", "inputs": ["a", "b"], "output": "u", "params": {}}],
        "final_output": "u",
        "final_output_requirements": {"grain": "detail", "required_columns": ["x"]},
    }
    before = json.dumps(plan, sort_keys=True)

    def fake(prompt, **kwargs):  # noqa: ANN001
        del prompt, kwargs
        return {"verdict": "pass", "reason_code": "satisfied", "evidence": []}

    run_semantic_verification(
        user_prompt="stack rows", plan=plan, variant="V1", chat_json_fn=fake
    )
    assert json.dumps(plan, sort_keys=True) == before


def test_no_scenario_hardcoding_in_verifier_module() -> None:
    from pathlib import Path

    src = Path("core/integrate/semantic_verifier.py").read_text(encoding="utf-8")
    for banned in ("three_file", "same_schema", "composite_key", "customer_name"):
        assert banned not in src
    assert "Do NOT rewrite" in VERIFIER_SYSTEM_PROMPT or "Do not rewrite" in VERIFIER_SYSTEM_PROMPT.lower() or "JUDGE" in VERIFIER_SYSTEM_PROMPT
