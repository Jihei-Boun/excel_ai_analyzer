"""Phase 39O — attempt lineage / verifier attribution tests.

Observability only. Does not assert semantic answer correctness.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core.integrate.attempt_lineage import (
    DISPOSITION_REJECTED_BY_SEMANTIC_VERIFIER,
    DISPOSITION_SUPERSEDED_BY_FAILURE_ESCALATION,
    DISPOSITION_SUPERSEDED_BY_SEMANTIC_ESCALATION,
    RequestAttemptLineage,
    STAGE_FAILURE_ESCALATION_SUCCESS,
    STAGE_FAST_PATH,
    STAGE_FAST_SUCCESS,
    STAGE_SEMANTIC_STRONG,
    TRIGGER_FAILURE_ESCALATION,
    TRIGGER_NONE,
    TRIGGER_SEMANTIC_ESCALATION,
    evaluate_attribution_regression,
    lineage_from_metadata,
    plan_fingerprint,
    safe_lineage_call,
)
from core.integrate.integration_plan_types import (
    FinalOutputRequirements,
    IntegrationPlan,
    IntegrationStep,
)
from core.integrate.integration_pipeline import IntegrationPipelineResult
from core.integrate.semantic_escalation import (
    SemanticEscalationConfig,
    run_integration_pipeline_semantic_experimental,
)
from core.integrate.semantic_verifier import (
    SemanticVerificationResult,
    run_semantic_verification,
)
from core.integrate.verifier_invocation_capture import (
    clear_last_record_for_tests,
    get_last_record_for_tests,
)


FAKE_DUAL = {
    "status": "planned",
    "steps": [
        {
            "op": "union_rows",
            "inputs": ["a", "b"],
            "output": "u",
            "params": {"column_policy": "aligned"},
        },
        {
            "op": "aggregate",
            "inputs": ["u"],
            "output": "out",
            "params": {
                "group_by": ["id"],
                "metrics": [
                    {"column": "x", "function": "sum", "alias": "left_total"},
                    {"column": "x", "function": "sum", "alias": "right_total"},
                ],
            },
        },
    ],
    "final_output": "out",
}

RENAME_JOIN = {
    "status": "planned",
    "steps": [
        {
            "op": "rename_columns",
            "inputs": ["a"],
            "output": "a1",
            "params": {"mapping": {"x": "left_x"}},
        },
        {
            "op": "rename_columns",
            "inputs": ["b"],
            "output": "b1",
            "params": {"mapping": {"x": "right_x"}},
        },
        {
            "op": "join",
            "inputs": ["a1", "b1"],
            "output": "out",
            "params": {"on": ["id"], "how": "inner"},
        },
    ],
    "final_output": "out",
}


def _make_plan(d: dict) -> IntegrationPlan:
    steps = [
        IntegrationStep(
            id=f"s{i}",
            op=s["op"],
            inputs=list(s.get("inputs") or []),
            output=str(s.get("output")),
            params=dict(s.get("params") or {}),
        )
        for i, s in enumerate(d.get("steps") or [])
    ]
    return IntegrationPlan(
        status="planned",
        steps=steps,
        final_output=d.get("final_output"),
        final_output_requirements=FinalOutputRequirements(
            grain="group", required_columns=["id"]
        ),
    )


@pytest.fixture(autouse=True)
def _clear_capture(monkeypatch):
    clear_last_record_for_tests()
    monkeypatch.delenv("MULTI_VERIFIER_CAPTURE_DIR", raising=False)
    monkeypatch.delenv("MULTI_VERIFIER_CAPTURE_ENABLED", raising=False)
    yield
    clear_last_record_for_tests()


def test_unique_attempt_identity():
    lin = RequestAttemptLineage(request_id="r1")
    a1 = lin.create_attempt(stage=STAGE_FAST_SUCCESS, plan=FAKE_DUAL)
    a2 = lin.create_attempt(stage=STAGE_SEMANTIC_STRONG, plan=RENAME_JOIN)
    assert a1.attempt_id != a2.attempt_id
    assert a1.seq == 1 and a2.seq == 2


def test_stable_attempt_across_execution_to_verifier(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(tmp_path))
    lin = RequestAttemptLineage(request_id="stable")
    att = lin.create_attempt(
        stage=STAGE_FAST_SUCCESS,
        plan=FAKE_DUAL,
        planner_model="qwen2.5:7b",
        planner_path="fast",
    )
    before = att.attempt_id
    fp = att.plan_fingerprint
    ctx = lin.capture_fields_for(att.attempt_id)

    def chat(prompt, *, system, base_url, model):
        return {
            "verdict": "fail",
            "reason_code": "wrong_output_grain",
            "evidence": ["x"],
        }

    run_semantic_verification(
        user_prompt="compare sides",
        plan=FAKE_DUAL,
        chat_json_fn=chat,
        source_schemas={"a": ["id", "x"], "b": ["id", "x"]},
        lineage_context=ctx,
    )
    rec = get_last_record_for_tests()
    assert rec is not None
    assert rec["attempt_id"] == before
    assert rec["plan_fingerprint"] == fp
    assert att.attempt_id == before


def test_verifier_invocation_links_to_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(tmp_path))
    lin = RequestAttemptLineage(request_id="link")
    att = lin.create_attempt(stage=STAGE_FAST_SUCCESS, plan=FAKE_DUAL)
    ctx = lin.capture_fields_for(att.attempt_id)

    def chat(prompt, *, system, base_url, model):
        return {"verdict": "pass", "reason_code": "satisfied", "evidence": ["ok"]}

    run_semantic_verification(
        user_prompt="q",
        plan=FAKE_DUAL,
        chat_json_fn=chat,
        source_schemas={"a": ["id"]},
        lineage_context=ctx,
    )
    rec = get_last_record_for_tests()
    assert rec["attempt_id"] == att.attempt_id
    inv = rec.get("verifier_invocation_id") or rec.get("invocation_id")
    assert inv
    lin.attach_verifier_invocation(att.attempt_id, inv)
    assert inv in att.verifier_invocation_ids


def test_plan_fingerprint_linkage():
    fp1 = plan_fingerprint(FAKE_DUAL)
    fp2 = plan_fingerprint(RENAME_JOIN)
    assert fp1 and fp2 and fp1 != fp2
    assert plan_fingerprint(FAKE_DUAL) == fp1


def test_semantic_escalation_parent_child():
    lin = RequestAttemptLineage(request_id="sem")
    a1 = lin.create_attempt(
        stage=STAGE_FAST_SUCCESS,
        plan=FAKE_DUAL,
        escalation_trigger=TRIGGER_NONE,
    )
    lin.set_disposition(a1.attempt_id, DISPOSITION_REJECTED_BY_SEMANTIC_VERIFIER)
    a2 = lin.create_attempt(
        stage=STAGE_SEMANTIC_STRONG,
        plan=RENAME_JOIN,
        parent_attempt_id=a1.attempt_id,
        escalation_trigger=TRIGGER_SEMANTIC_ESCALATION,
    )
    lin.set_disposition(a1.attempt_id, DISPOSITION_SUPERSEDED_BY_SEMANTIC_ESCALATION)
    lin.set_final(a2.attempt_id)
    assert a2.parent_attempt_id == a1.attempt_id
    assert a2.escalation_trigger == TRIGGER_SEMANTIC_ESCALATION
    assert lin.final_attempt_id == a2.attempt_id


def test_failure_escalation_distinguishable():
    lin = RequestAttemptLineage(request_id="fail")
    a1 = lin.create_attempt(stage=STAGE_FAST_PATH, plan=FAKE_DUAL)
    lin.set_disposition(a1.attempt_id, DISPOSITION_SUPERSEDED_BY_FAILURE_ESCALATION)
    a2 = lin.create_attempt(
        stage=STAGE_FAILURE_ESCALATION_SUCCESS,
        plan=RENAME_JOIN,
        parent_attempt_id=a1.attempt_id,
        escalation_trigger=TRIGGER_FAILURE_ESCALATION,
    )
    assert a2.escalation_trigger == TRIGGER_FAILURE_ESCALATION
    assert a2.escalation_trigger != TRIGGER_SEMANTIC_ESCALATION


def test_final_attempt_identification():
    lin = RequestAttemptLineage(request_id="fin")
    a1 = lin.create_attempt(stage=STAGE_FAST_SUCCESS, plan=FAKE_DUAL)
    a2 = lin.create_attempt(
        stage=STAGE_SEMANTIC_STRONG,
        plan=RENAME_JOIN,
        parent_attempt_id=a1.attempt_id,
        escalation_trigger=TRIGGER_SEMANTIC_ESCALATION,
    )
    lin.set_final(a2.attempt_id)
    assert lin.final_attempt_id == a2.attempt_id
    assert a2.disposition == "final"
    assert a1.disposition != "final"


def test_multiple_verifier_invocations_same_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(tmp_path))
    lin = RequestAttemptLineage(request_id="multi-v")
    att = lin.create_attempt(stage=STAGE_FAST_SUCCESS, plan=FAKE_DUAL)
    ctx = lin.capture_fields_for(att.attempt_id)

    def chat(prompt, *, system, base_url, model):
        return {"verdict": "uncertain", "reason_code": "insufficient", "evidence": []}

    ids = []
    for _ in range(2):
        run_semantic_verification(
            user_prompt="q",
            plan=FAKE_DUAL,
            chat_json_fn=chat,
            source_schemas={"a": ["id"]},
            lineage_context=ctx,
        )
        rec = get_last_record_for_tests()
        ids.append(rec.get("verifier_invocation_id") or rec.get("invocation_id"))
        assert rec["attempt_id"] == att.attempt_id
    assert ids[0] != ids[1]


def test_phase39m_attribution_regression_fixture():
    """Mandatory: final correct + A1 FAIL must NOT be reported as verifier FF."""
    lin = RequestAttemptLineage(request_id="P39M-shape")
    a1 = lin.create_attempt(stage=STAGE_FAST_SUCCESS, plan=FAKE_DUAL)
    lin.attach_verifier_invocation(a1.attempt_id, "v-a1")
    lin.set_disposition(a1.attempt_id, DISPOSITION_REJECTED_BY_SEMANTIC_VERIFIER)
    a2 = lin.create_attempt(
        stage=STAGE_SEMANTIC_STRONG,
        plan=RENAME_JOIN,
        parent_attempt_id=a1.attempt_id,
        escalation_trigger=TRIGGER_SEMANTIC_ESCALATION,
    )
    lin.set_final(a2.attempt_id)
    out = evaluate_attribution_regression(
        verified_attempt_id=a1.attempt_id,
        verified_plan_fingerprint=a1.plan_fingerprint,
        verified_verdict="fail",
        final_attempt_id=a2.attempt_id,
        final_plan_fingerprint=a2.plan_fingerprint,
        final_shadow_correct=True,
    )
    assert out["invalid_false_fail_shortcut"] is True
    assert out["same_attempt"] is False


def test_telemetry_failure_does_not_affect_semantics():
    def boom():
        raise RuntimeError("lineage boom")

    err = safe_lineage_call(boom)
    assert "lineage_error" in err

    def chat(prompt, *, system, base_url, model):
        return {"verdict": "pass", "reason_code": "satisfied", "evidence": ["ok"]}

    out = run_semantic_verification(
        user_prompt="q",
        plan=FAKE_DUAL,
        chat_json_fn=chat,
        source_schemas={"a": ["id"]},
        lineage_context=None,
    )
    assert out.verdict == "pass"


def test_serialization_roundtrip():
    lin = RequestAttemptLineage(request_id="ser")
    a1 = lin.create_attempt(stage=STAGE_FAST_SUCCESS, plan=FAKE_DUAL)
    lin.set_final(a1.attempt_id)
    d = lin.to_dict()
    assert d["final_attempt_id"] == a1.attempt_id
    assert d["attempts"][0]["plan_fingerprint"]


def test_backward_compat_missing_lineage():
    assert lineage_from_metadata({}) is None
    assert lineage_from_metadata(None) is None
    block = lineage_from_metadata({"attempt_lineage": {"request_id": "x"}})
    assert block is not None
    assert block.get("request_id") == "x"


def test_escalation_pipeline_wires_lineage(monkeypatch, tmp_path):
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(tmp_path))
    fake = _make_plan(FAKE_DUAL)
    good = _make_plan(RENAME_JOIN)
    base = IntegrationPipelineResult(status="success", plan=fake, metadata={})

    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_integration_pipeline",
        lambda *a, **k: base,
    )

    def fake_verify(**kwargs):
        assert kwargs.get("lineage_context") is not None
        assert kwargs["lineage_context"].get("attempt_id")
        return SemanticVerificationResult(
            verdict="fail",
            reason_code="wrong_output_grain",
            evidence=["fake dual"],
            parse_ok=True,
        )

    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_semantic_verification",
        fake_verify,
    )

    strong = IntegrationPipelineResult(status="success", plan=good, metadata={})
    monkeypatch.setattr(
        "core.integrate.semantic_escalation._run_integration_attempt_loop",
        lambda *a, **k: strong,
    )

    out = run_integration_pipeline_semantic_experimental(
        "compare left and right",
        {
            "a": pd.DataFrame({"id": [1], "x": [1]}),
            "b": pd.DataFrame({"id": [1], "x": [2]}),
        },
        {},
        config=SemanticEscalationConfig(enable_semantic_escalation=True),
    )
    meta = out.metadata or {}
    assert meta.get("final_attempt_id")
    assert meta.get("verified_attempt_id")
    assert meta["final_attempt_id"] != meta["verified_attempt_id"]
    assert meta.get("verified_plan_fingerprint") != meta.get("final_plan_fingerprint")
    lin = meta.get("attempt_lineage") or {}
    assert len(lin.get("attempts") or []) >= 2
