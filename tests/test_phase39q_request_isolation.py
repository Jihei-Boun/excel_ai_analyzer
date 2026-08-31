"""Phase 39Q — Shadow request isolation & async lineage integrity.

Deterministic provenance tests. No semantic correctness assertions.
Uses Events/Barriers; avoids sleep-only flakiness.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from core.integrate.attempt_lineage import (
    DISPOSITION_REJECTED_BY_SEMANTIC_VERIFIER,
    DISPOSITION_SUPERSEDED_BY_SEMANTIC_ESCALATION,
    RequestAttemptLineage,
    STAGE_FAST_SUCCESS,
    STAGE_SEMANTIC_STRONG,
    TRIGGER_SEMANTIC_ESCALATION,
    lineage_from_metadata,
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
    get_integrity_failures_for_tests,
    get_record_for_attempt,
    update_last_escalation,
    update_last_lineage_finalization,
)
from core.routing.route_multi import route_multi_prompt
from core.routing.route_types import SingleRouteOutcome
from core.shadow.config import ShadowConfig, load_shadow_config
from core.shadow.hook import finish_with_shadow
from core.shadow.isolation import (
    bind_records_by_request_id,
    capture_integrity_report,
    lineage_integrity_report,
    p39p_contamination_shape,
    select_record_for_request,
)
from core.shadow.snapshot import build_shadow_snapshot
from core.shadow.worker import (
    get_inflight_for_tests,
    reset_shadow_worker_for_tests,
    schedule_shadow,
    set_force_runner_for_tests,
)


FAKE_DUAL = {
    "status": "planned",
    "steps": [
        {
            "op": "union_rows",
            "inputs": ["a", "b"],
            "output": "u",
            "params": {"column_policy": "aligned"},
        }
    ],
    "final_output": "u",
}

RENAME_JOIN = {
    "status": "planned",
    "steps": [
        {
            "op": "join",
            "inputs": ["a", "b"],
            "output": "out",
            "params": {"on": ["id"], "how": "inner"},
        }
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
def _reset(monkeypatch, tmp_path):
    reset_shadow_worker_for_tests()
    set_force_runner_for_tests(None)
    clear_last_record_for_tests()
    monkeypatch.delenv("MULTI_SHADOW_ENABLED", raising=False)
    monkeypatch.delenv("MULTI_SHADOW_INLINE_FOR_TESTS", raising=False)
    monkeypatch.delenv("MULTI_VERIFIER_CAPTURE_DIR", raising=False)
    monkeypatch.delenv("MULTI_VERIFIER_CAPTURE_ENABLED", raising=False)
    monkeypatch.delenv("MULTI_VERIFIER_CAPTURE_REQUEST_ID", raising=False)
    monkeypatch.delenv("MULTI_VERIFIER_CAPTURE_CASE_ID", raising=False)
    yield
    reset_shadow_worker_for_tests()
    set_force_runner_for_tests(None)
    clear_last_record_for_tests()


def _frames() -> list[tuple[str, pd.DataFrame]]:
    return [
        ("a", pd.DataFrame({"id": [1], "x": [10]})),
        ("b", pd.DataFrame({"id": [1], "y": [3]})),
    ]


def _cfg(tmp_path: Path, **kwargs) -> ShadowConfig:
    base = dict(
        enabled=True,
        telemetry_dir=tmp_path,
        sample_rate=1.0,
        max_concurrency=2,
        queue_size=8,
        inline_for_tests=False,
        store_prompt=True,
        timeout_sec=60.0,
    )
    base.update(kwargs)
    return ShadowConfig(**base)


def _drain(timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    while get_inflight_for_tests() > 0 and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.05)


def _load_tel(path: Path) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    for p in sorted(path.glob("shadow_*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs


def _sentinel_out(snapshot, *, status: str = "success", fail: bool = False) -> dict[str, Any]:
    rid = snapshot.request_id
    prompt = snapshot.user_prompt
    aid = f"{rid}:A1:fixed001"
    if fail:
        raise RuntimeError(f"worker_boom_{rid}")
    return {
        "shadow_started": True,
        "shadow_completed": True,
        "shadow_status": status,
        "shadow_success": status == "success",
        "latency_total_s": 0.01,
        "final_plan": {"sentinel": f"PLAN_{rid}", "prompt_echo": prompt},
        "result_fingerprint": {"columns": [f"RESULT_{rid}"]},
        "attempt_lineage": {
            "request_id": rid,
            "final_attempt_id": aid,
            "attempts": [
                {
                    "attempt_id": aid,
                    "request_id": rid,
                    "plan_fingerprint": f"FP_{rid}",
                    "result_fingerprint": f"RF_{rid}",
                    "parent_attempt_id": None,
                }
            ],
        },
        "verified_attempt_id": aid,
        "final_attempt_id": aid,
        "verified_plan_fingerprint": f"FP_{rid}",
        "final_plan_fingerprint": f"FP_{rid}",
        "semantic_verifier_invoked": True,
        "semantic_verifier_verdict": "pass",
    }


def _schedule(cfg, request_id: str, prompt: str) -> None:
    snap = build_shadow_snapshot(
        prompt=prompt,
        named_frames=_frames(),
        base_url="http://x",
        model="m",
        request_id=request_id,
        case_id=f"case-{request_id}",
        store_prompt=True,
    )
    schedule_shadow(
        snap,
        legacy_observation={"legacy_success": True, "result_fingerprint": None},
        config=cfg,
    )


def _assert_bound(recs: list[dict[str, Any]], rid: str, prompt: str) -> dict[str, Any]:
    rec = select_record_for_request(recs, rid)
    assert rec is not None, f"missing telemetry for {rid}"
    assert rec["request_id"] == rid
    assert rec.get("prompt") == prompt
    shadow = rec.get("shadow") or {}
    plan = shadow.get("final_plan") or {}
    assert plan.get("sentinel") == f"PLAN_{rid}"
    assert plan.get("prompt_echo") == prompt
    cols = ((shadow.get("result_fingerprint") or {}).get("columns")) or []
    assert f"RESULT_{rid}" in cols
    lin = shadow.get("attempt_lineage") or {}
    report = lineage_integrity_report(
        request_id=rid,
        lineage=lin,
        verified_attempt_id=shadow.get("verified_attempt_id"),
        final_attempt_id=shadow.get("final_attempt_id"),
    )
    assert report["ok"], report
    assert not rec.get("provenance_integrity_failure")
    return rec


# ---------------------------------------------------------------------------
# 1. Sequential isolation
# ---------------------------------------------------------------------------


def test_sequential_request_isolation(tmp_path):
    cfg = _cfg(tmp_path, max_concurrency=1, inline_for_tests=True)
    set_force_runner_for_tests(lambda snap, config=None: _sentinel_out(snap))
    _schedule(cfg, "req-A", "PROMPT_A")
    _schedule(cfg, "req-B", "PROMPT_B")
    recs = _load_tel(tmp_path)
    _assert_bound(recs, "req-A", "PROMPT_A")
    _assert_bound(recs, "req-B", "PROMPT_B")
    bound = bind_records_by_request_id(recs)
    assert set(bound) == {"req-A", "req-B"}


# ---------------------------------------------------------------------------
# 2–4. Timeout + late completion
# ---------------------------------------------------------------------------


def test_timeout_late_completion_does_not_cross_bind(tmp_path):
    cfg = _cfg(tmp_path, max_concurrency=2)
    started_a = threading.Event()
    release_a = threading.Event()

    def runner(snapshot, config=None):  # noqa: ANN001
        if snapshot.request_id == "req-A":
            started_a.set()
            assert release_a.wait(timeout=5)
        return _sentinel_out(snapshot)

    set_force_runner_for_tests(runner)
    _schedule(cfg, "req-A", "PROMPT_A")
    assert started_a.wait(timeout=3)
    # Caller treats A as timed out / finished and starts B while A still runs.
    _schedule(cfg, "req-B", "PROMPT_B")
    release_a.set()
    _drain()
    recs = _load_tel(tmp_path)
    _assert_bound(recs, "req-A", "PROMPT_A")
    _assert_bound(recs, "req-B", "PROMPT_B")
    shape = p39p_contamination_shape(
        request_b_id="req-B",
        request_a_attempt_id="req-A:A1:fixed001",
        request_b_verified_attempt_id=(
            (select_record_for_request(recs, "req-B") or {}).get("shadow") or {}
        ).get("verified_attempt_id"),
        request_a_final_attempt_id=(
            (select_record_for_request(recs, "req-A") or {}).get("shadow") or {}
        ).get("final_attempt_id"),
        request_b_attempt_id="req-B:A1:fixed001",
    )
    assert shape["contaminated"] is False


def test_late_completion_during_next_request(tmp_path):
    cfg = _cfg(tmp_path, max_concurrency=2)
    a_started = threading.Event()
    b_started = threading.Event()
    release_a = threading.Event()
    release_b = threading.Event()

    def runner(snapshot, config=None):  # noqa: ANN001
        if snapshot.request_id == "req-A":
            a_started.set()
            assert release_a.wait(timeout=5)
        else:
            b_started.set()
            assert release_b.wait(timeout=5)
        return _sentinel_out(snapshot)

    set_force_runner_for_tests(runner)
    _schedule(cfg, "req-A", "PROMPT_A")
    assert a_started.wait(timeout=3)
    _schedule(cfg, "req-B", "PROMPT_B")
    assert b_started.wait(timeout=3)
    # A finishes while B is in-flight (B "verifier" stage analogue).
    release_a.set()
    time.sleep(0.05)
    release_b.set()
    _drain()
    recs = _load_tel(tmp_path)
    _assert_bound(recs, "req-A", "PROMPT_A")
    _assert_bound(recs, "req-B", "PROMPT_B")


def test_late_verifier_and_capture_write(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_REQUEST_ID", "req-A")
    snap = build_shadow_snapshot(
        prompt="PROMPT_A",
        named_frames=_frames(),
        base_url="http://x",
        model="m",
        request_id="req-A",
        case_id="case-A",
    )
    entered = threading.Event()
    release = threading.Event()

    def slow_chat(prompt, *, system, base_url, model):  # noqa: ANN001
        entered.set()
        assert release.wait(timeout=5)
        return {"verdict": "pass", "reason_code": "satisfied", "evidence": ["ok"]}

    lin = RequestAttemptLineage(request_id="req-A", case_id="case-A")
    att = lin.create_attempt(stage=STAGE_FAST_SUCCESS, plan=FAKE_DUAL)
    ctx = lin.capture_fields_for(att.attempt_id)

    def worker() -> None:
        run_semantic_verification(
            user_prompt="PROMPT_A",
            plan=FAKE_DUAL,
            chat_json_fn=slow_chat,
            source_schemas={"a": ["id"]},
            lineage_context=ctx,
        )

    t = threading.Thread(target=worker)
    t.start()
    assert entered.wait(timeout=3)
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_REQUEST_ID", "req-B")
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_CASE_ID", "case-B")
    release.set()
    t.join(timeout=5)
    rec = get_record_for_attempt(att.attempt_id)
    assert rec is not None
    assert rec["request_id"] == "req-A"
    assert rec["attempt_id"] == att.attempt_id
    assert rec["case_id"] == "case-A"
    cap_rep = capture_integrity_report(
        capture=rec,
        request_id="req-A",
        attempt_id=att.attempt_id,
        plan_fingerprint=att.plan_fingerprint,
    )
    assert cap_rep["ok"], cap_rep
    assert snap.request_id == "req-A"


# ---------------------------------------------------------------------------
# 5–8. Escalation / exception / backlog / inversion
# ---------------------------------------------------------------------------


def test_semantic_escalation_overlap_parent_stays_on_a(monkeypatch, tmp_path):
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(tmp_path))
    base = IntegrationPipelineResult(status="success", plan=_make_plan(FAKE_DUAL), metadata={})
    strong = IntegrationPipelineResult(
        status="success", plan=_make_plan(RENAME_JOIN), metadata={}
    )
    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_integration_pipeline",
        lambda *a, **k: base,
    )
    delay_strong = threading.Event()
    b_started = threading.Event()

    def fake_verify(**kwargs):  # noqa: ANN001
        return SemanticVerificationResult(
            verdict="fail",
            reason_code="wrong_output_grain",
            evidence=["x"],
            parse_ok=True,
            verifier_invocation_id="inv-A1",
        )

    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_semantic_verification",
        fake_verify,
    )

    def slow_strong(*a, **k):  # noqa: ANN001
        b_started.set()
        delay_strong.wait(timeout=5)
        return strong

    monkeypatch.setattr(
        "core.integrate.semantic_escalation._run_integration_attempt_loop",
        slow_strong,
    )

    os.environ["MULTI_VERIFIER_CAPTURE_REQUEST_ID"] = "req-B"
    result_holder: dict[str, Any] = {}

    def run_a() -> None:
        result_holder["a"] = run_integration_pipeline_semantic_experimental(
            "PROMPT_A",
            {"a": pd.DataFrame({"id": [1], "x": [1]}), "b": pd.DataFrame({"id": [1], "x": [2]})},
            {},
            config=SemanticEscalationConfig(enable_semantic_escalation=True),
            request_id="req-A",
            case_id="case-A",
        )

    t = threading.Thread(target=run_a)
    t.start()
    assert b_started.wait(timeout=3)
    # B starts while A's strong attempt is delayed.
    b_lin = RequestAttemptLineage(request_id="req-B")
    b1 = b_lin.create_attempt(stage=STAGE_FAST_SUCCESS, plan=RENAME_JOIN)
    b_lin.set_final(b1.attempt_id)
    delay_strong.set()
    t.join(timeout=5)
    meta = (result_holder["a"].metadata or {})
    assert str(meta.get("verified_attempt_id") or "").startswith("req-A")
    assert str(meta.get("final_attempt_id") or "").startswith("req-A")
    lin = meta.get("attempt_lineage") or {}
    assert lin.get("request_id") == "req-A"
    for att in lin.get("attempts") or []:
        assert att.get("request_id") == "req-A"
        if att.get("parent_attempt_id"):
            parent = next(
                x for x in lin["attempts"] if x["attempt_id"] == att["parent_attempt_id"]
            )
            assert parent["request_id"] == "req-A"
    assert b_lin.final_attempt_id == b1.attempt_id
    assert b1.request_id == "req-B"


def test_failure_escalation_and_exception_overlap(tmp_path):
    cfg = _cfg(tmp_path, max_concurrency=2)
    a_started = threading.Event()
    b_started = threading.Event()
    release_a = threading.Event()

    def runner(snapshot, config=None):  # noqa: ANN001
        if snapshot.request_id == "req-A":
            a_started.set()
            assert release_a.wait(timeout=5)
            raise RuntimeError("A_worker_exception")
        b_started.set()
        return _sentinel_out(snapshot)

    set_force_runner_for_tests(runner)
    _schedule(cfg, "req-A", "PROMPT_A")
    assert a_started.wait(timeout=3)
    _schedule(cfg, "req-B", "PROMPT_B")
    assert b_started.wait(timeout=3)
    release_a.set()
    _drain()
    recs = _load_tel(tmp_path)
    rec_a = select_record_for_request(recs, "req-A")
    rec_b = select_record_for_request(recs, "req-B")
    assert rec_a is not None and rec_b is not None
    assert rec_a["request_id"] == "req-A"
    assert (rec_a.get("shadow") or {}).get("error_family") == "shadow_infrastructure_error"
    _assert_bound(recs, "req-B", "PROMPT_B")


def test_multiple_timeout_backlog(tmp_path):
    cfg = _cfg(tmp_path, max_concurrency=2, queue_size=8)
    releases = {k: threading.Event() for k in ("req-A", "req-B", "req-C")}
    started = {k: threading.Event() for k in releases}

    def runner(snapshot, config=None):  # noqa: ANN001
        started[snapshot.request_id].set()
        assert releases[snapshot.request_id].wait(timeout=5)
        return _sentinel_out(snapshot)

    set_force_runner_for_tests(runner)
    _schedule(cfg, "req-A", "PROMPT_A")
    _schedule(cfg, "req-B", "PROMPT_B")
    _schedule(cfg, "req-C", "PROMPT_C")
    assert started["req-A"].wait(timeout=3)
    # A and B "time out" from caller; C starts; A/B complete late.
    releases["req-A"].set()
    releases["req-B"].set()
    releases["req-C"].set()
    _drain()
    recs = _load_tel(tmp_path)
    _assert_bound(recs, "req-A", "PROMPT_A")
    _assert_bound(recs, "req-B", "PROMPT_B")
    _assert_bound(recs, "req-C", "PROMPT_C")
    rec_c = select_record_for_request(recs, "req-C")
    shadow_c = (rec_c or {}).get("shadow") or {}
    assert shadow_c.get("verified_attempt_id") != "req-A:A1:fixed001"
    assert shadow_c.get("verified_attempt_id") != "req-B:A1:fixed001"


def test_completion_order_inversion_identity_binding(tmp_path):
    cfg = _cfg(tmp_path, max_concurrency=3)
    order = ["req-A", "req-B", "req-C"]
    releases = {k: threading.Event() for k in order}
    started = {k: threading.Event() for k in order}

    def runner(snapshot, config=None):  # noqa: ANN001
        started[snapshot.request_id].set()
        assert releases[snapshot.request_id].wait(timeout=5)
        return _sentinel_out(snapshot)

    set_force_runner_for_tests(runner)
    for rid, prompt in (("req-A", "PROMPT_A"), ("req-B", "PROMPT_B"), ("req-C", "PROMPT_C")):
        _schedule(cfg, rid, prompt)
    for rid in order:
        assert started[rid].wait(timeout=3)
    # Complete C, A, B — opposite of submission order.
    for rid in ("req-C", "req-A", "req-B"):
        releases[rid].set()
    _drain()
    recs = _load_tel(tmp_path)
    by_id = bind_records_by_request_id(recs)
    assert by_id["req-A"]["prompt"] == "PROMPT_A"
    assert by_id["req-B"]["prompt"] == "PROMPT_B"
    assert by_id["req-C"]["prompt"] == "PROMPT_C"
    # Completion-order zip against submission order is not a valid collector.
    submission = ["req-A", "req-B", "req-C"]
    observed_order = [r["request_id"] for r in recs]
    zipped_wrong = [
        recs[i]["prompt"]
        for i, rid in enumerate(submission)
        if i < len(recs) and recs[i]["request_id"] != rid
    ]
    # If inversion occurred, zip would mis-attribute; identity map must still hold.
    if observed_order != submission:
        assert zipped_wrong
    assert select_record_for_request(recs, "req-A")["request_id"] == "req-A"


# ---------------------------------------------------------------------------
# 11–17. Lineage / capture / fingerprint invariants
# ---------------------------------------------------------------------------


def test_request_id_consistency_across_attempt_lineage():
    lin = RequestAttemptLineage(request_id="req-A")
    a1 = lin.create_attempt(stage=STAGE_FAST_SUCCESS, plan=FAKE_DUAL)
    a2 = lin.create_attempt(
        stage=STAGE_SEMANTIC_STRONG,
        plan=RENAME_JOIN,
        parent_attempt_id=a1.attempt_id,
        escalation_trigger=TRIGGER_SEMANTIC_ESCALATION,
    )
    lin.set_disposition(a1.attempt_id, DISPOSITION_REJECTED_BY_SEMANTIC_VERIFIER)
    lin.set_disposition(a1.attempt_id, DISPOSITION_SUPERSEDED_BY_SEMANTIC_ESCALATION)
    lin.set_final(a2.attempt_id)
    assert a1.request_id == a2.request_id == "req-A"
    assert a2.parent_attempt_id == a1.attempt_id
    report = lineage_integrity_report(
        request_id="req-A",
        lineage=lin.to_dict(),
        verified_attempt_id=a1.attempt_id,
        final_attempt_id=a2.attempt_id,
    )
    assert report["ok"]
    # Refuse marking a foreign attempt as final.
    lin.set_final("req-B:A1:nope")
    assert lin.final_attempt_id == a2.attempt_id
    assert lin.integrity_violations


def test_parent_child_verifier_capture_final_same_request(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(tmp_path))
    lin = RequestAttemptLineage(request_id="req-A", case_id="case-A")
    a1 = lin.create_attempt(stage=STAGE_FAST_SUCCESS, plan=FAKE_DUAL)
    ctx = lin.capture_fields_for(a1.attempt_id)

    def chat(prompt, *, system, base_url, model):  # noqa: ANN001
        return {"verdict": "pass", "reason_code": "satisfied", "evidence": ["ok"]}

    out = run_semantic_verification(
        user_prompt="PROMPT_A",
        plan=FAKE_DUAL,
        chat_json_fn=chat,
        source_schemas={"a": ["id"]},
        lineage_context=ctx,
    )
    rec = get_record_for_attempt(a1.attempt_id)
    assert rec["request_id"] == "req-A"
    assert rec["attempt_id"] == a1.attempt_id
    assert rec["plan_fingerprint"] == a1.plan_fingerprint
    assert out.verifier_invocation_id == rec["verifier_invocation_id"]
    lin.attach_verifier_invocation(a1.attempt_id, rec["verifier_invocation_id"])
    lin.set_final(a1.attempt_id)
    update_last_lineage_finalization(
        became_final=True,
        final_attempt_id=a1.attempt_id,
        attempt_disposition="final",
        request_id="req-A",
        attempt_id=a1.attempt_id,
    )
    rec2 = get_record_for_attempt(a1.attempt_id)
    assert rec2["final_attempt_id"] == a1.attempt_id
    assert rec2["request_id"] == "req-A"


def test_capture_refuses_cross_request_finalization(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(tmp_path))

    def chat_pass(prompt, *, system, base_url, model):  # noqa: ANN001
        return {"verdict": "pass", "reason_code": "satisfied", "evidence": ["ok"]}

    lin_a = RequestAttemptLineage(request_id="req-A")
    a1 = lin_a.create_attempt(stage=STAGE_FAST_SUCCESS, plan=FAKE_DUAL)
    run_semantic_verification(
        user_prompt="PROMPT_A",
        plan=FAKE_DUAL,
        chat_json_fn=chat_pass,
        source_schemas={"a": ["id"]},
        lineage_context=lin_a.capture_fields_for(a1.attempt_id),
    )
    lin_b = RequestAttemptLineage(request_id="req-B")
    b1 = lin_b.create_attempt(stage=STAGE_FAST_SUCCESS, plan=RENAME_JOIN)
    run_semantic_verification(
        user_prompt="PROMPT_B",
        plan=RENAME_JOIN,
        chat_json_fn=chat_pass,
        source_schemas={"a": ["id"]},
        lineage_context=lin_b.capture_fields_for(b1.attempt_id),
    )
    rec_b_before = dict(get_record_for_attempt(b1.attempt_id) or {})
    # A's late finalization must not mutate B's capture.
    update_last_lineage_finalization(
        became_final=True,
        final_attempt_id=a1.attempt_id,
        request_id="req-A",
        attempt_id=a1.attempt_id,
    )
    update_last_escalation(
        escalation_triggered=True,
        escalation_type="semantic_verifier_fail",
        request_id="req-A",
        attempt_id=a1.attempt_id,
    )
    rec_b = get_record_for_attempt(b1.attempt_id)
    assert rec_b["request_id"] == "req-B"
    assert rec_b["attempt_id"] == b1.attempt_id
    assert rec_b.get("final_attempt_id") == rec_b_before.get("final_attempt_id")
    rec_a = get_record_for_attempt(a1.attempt_id)
    assert rec_a["request_id"] == "req-A"
    # Attempting to finalize B's capture under A's identity is refused.
    update_last_lineage_finalization(
        became_final=True,
        final_attempt_id="req-A:stolen",
        request_id="req-A",
        attempt_id=b1.attempt_id,
    )
    failures = get_integrity_failures_for_tests()
    assert any(f.get("action") == "refused_rebind" for f in failures)
    rec_b2 = get_record_for_attempt(b1.attempt_id)
    assert rec_b2.get("final_attempt_id") != "req-A:stolen"


def test_plan_and_result_fingerprint_consistency():
    lin = RequestAttemptLineage(request_id="req-A")
    a1 = lin.create_attempt(
        stage=STAGE_FAST_SUCCESS,
        plan=FAKE_DUAL,
        result_fingerprint="RF_A",
    )
    cap = {
        "request_id": "req-A",
        "attempt_id": a1.attempt_id,
        "plan_fingerprint": a1.plan_fingerprint,
        "result_fingerprint": "RF_A",
    }
    assert capture_integrity_report(
        capture=cap,
        request_id="req-A",
        attempt_id=a1.attempt_id,
        plan_fingerprint=a1.plan_fingerprint,
        result_fingerprint="RF_A",
    )["ok"]
    bad = dict(cap, plan_fingerprint="FP_B")
    assert not capture_integrity_report(
        capture=bad,
        request_id="req-A",
        attempt_id=a1.attempt_id,
        plan_fingerprint=a1.plan_fingerprint,
    )["ok"]


# ---------------------------------------------------------------------------
# 18–22. Collector, historical, telemetry failure, Legacy, Shadow OFF
# ---------------------------------------------------------------------------


def test_collector_identity_not_completion_order():
    recs = [
        {"request_id": "req-C", "prompt": "PROMPT_C"},
        {"request_id": "req-A", "prompt": "PROMPT_A"},
        {"request_id": "req-B", "prompt": "PROMPT_B"},
    ]
    bound = bind_records_by_request_id(recs)
    assert bound["req-A"]["prompt"] == "PROMPT_A"
    zipped = list(zip(["req-A", "req-B", "req-C"], recs, strict=True))
    # Completion-order zip is the 39P harness defect.
    assert zipped[0][1]["prompt"] != "PROMPT_A"


def test_historical_telemetry_backward_compatible():
    old = {"attempt_lineage": {"request_id": "x", "attempts": []}}
    block = lineage_from_metadata(old)
    assert block.get("request_id") == "x"
    assert "integrity_violations" not in block
    report = lineage_integrity_report(request_id="x", lineage=block)
    assert report["ok"]


def test_telemetry_failure_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(tmp_path / "missing_parent" / "cap"))
    # Parent will be created by persist; force write failure via a file-as-dir.
    bad = tmp_path / "not_a_dir"
    bad.write_text("x", encoding="utf-8")
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(bad))
    out = run_semantic_verification(
        user_prompt="q",
        plan=FAKE_DUAL,
        chat_json_fn=lambda prompt, *, system, base_url, model: {
            "verdict": "fail",
            "reason_code": "wrong_output_grain",
            "evidence": ["x"],
        },
        source_schemas={"a": ["id"]},
        lineage_context={"request_id": "req-A", "attempt_id": "req-A:A1:x"},
    )
    assert out.verdict == "fail"
    assert out.reason_code == "wrong_output_grain"


def test_legacy_isolation_late_shadow_does_not_change_outcome(tmp_path):
    cfg = _cfg(tmp_path, max_concurrency=2)
    started = threading.Event()
    release = threading.Event()

    def runner(snapshot, config=None):  # noqa: ANN001
        started.set()
        release.wait(timeout=5)
        return _sentinel_out(snapshot)

    set_force_runner_for_tests(runner)
    snap = build_shadow_snapshot(
        prompt="PROMPT_A",
        named_frames=_frames(),
        base_url="http://x",
        model="m",
        request_id="req-A",
        store_prompt=True,
    )
    outcome = SingleRouteOutcome(
        reply="legacy-ok",
        dataframe=pd.DataFrame({"id": [1]}),
        operation_name="structured_integrate",
    )
    returned = finish_with_shadow(outcome, snapshot=snap, config=cfg)
    assert returned is outcome
    assert returned.reply == "legacy-ok"
    assert started.wait(timeout=3)
    # Caller already has Legacy result; late Shadow must not mutate it.
    outcome.reply = "legacy-ok"
    release.set()
    _drain()
    assert returned.reply == "legacy-ok"
    recs = _load_tel(tmp_path)
    _assert_bound(recs, "req-A", "PROMPT_A")


def test_shadow_default_off():
    assert os.environ.get("MULTI_SHADOW_ENABLED") in {None, "", "false", "0"}
    cfg = load_shadow_config()
    assert cfg.enabled is False


def test_env_overwrite_cannot_rebind_frozen_snapshot(monkeypatch):
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_REQUEST_ID", "req-A")
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_CASE_ID", "case-A")
    snap = build_shadow_snapshot(
        prompt="PROMPT_A",
        named_frames=_frames(),
        base_url="http://x",
        model="m",
    )
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_REQUEST_ID", "req-B")
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_CASE_ID", "case-B")
    assert snap.request_id == "req-A"
    assert snap.case_id == "case-A"
    # Old Phase 39P mechanism: lineage read live env *after* overwrite.
    from core.integrate.verifier_invocation_capture import env_case_id, env_request_id

    assert env_request_id() == "req-B"
    assert env_case_id() == "case-B"


def test_pipeline_uses_frozen_request_id_not_live_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_REQUEST_ID", "req-B")
    base = IntegrationPipelineResult(status="success", plan=_make_plan(RENAME_JOIN), metadata={})
    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_integration_pipeline",
        lambda *a, **k: base,
    )
    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_semantic_verification",
        lambda **k: SemanticVerificationResult(
            verdict="pass", reason_code="satisfied", evidence=["ok"], parse_ok=True
        ),
    )
    out = run_integration_pipeline_semantic_experimental(
        "PROMPT_A",
        {"a": pd.DataFrame({"id": [1], "x": [1]}), "b": pd.DataFrame({"id": [1], "x": [2]})},
        {},
        config=SemanticEscalationConfig(enable_semantic_escalation=True),
        request_id="req-A",
        case_id="case-A",
    )
    lin = (out.metadata or {}).get("attempt_lineage") or {}
    assert lin.get("request_id") == "req-A"
    assert str((out.metadata or {}).get("final_attempt_id") or "").startswith("req-A")
    assert str((out.metadata or {}).get("verified_attempt_id") or "").startswith("req-A")


def test_p39p_contamination_shape_prevented(tmp_path):
    """Mandatory regression: B.verified_attempt_id must never be A1."""
    cfg = _cfg(tmp_path, max_concurrency=2)
    a_started = threading.Event()
    b_in_verifier = threading.Event()
    release_a = threading.Event()
    release_b = threading.Event()

    def runner(snapshot, config=None):  # noqa: ANN001
        if snapshot.request_id == "req-A":
            a_started.set()
            assert release_a.wait(timeout=5)
            return _sentinel_out(snapshot)
        b_in_verifier.set()
        assert release_b.wait(timeout=5)
        return _sentinel_out(snapshot)

    set_force_runner_for_tests(runner)
    _schedule(cfg, "req-A", "PROMPT_A")
    assert a_started.wait(timeout=3)
    _schedule(cfg, "req-B", "PROMPT_B")
    assert b_in_verifier.wait(timeout=3)
    release_a.set()
    release_b.set()
    _drain()
    recs = _load_tel(tmp_path)
    a_rec = select_record_for_request(recs, "req-A")
    b_rec = select_record_for_request(recs, "req-B")
    a_aid = ((a_rec or {}).get("shadow") or {}).get("verified_attempt_id")
    b_vid = ((b_rec or {}).get("shadow") or {}).get("verified_attempt_id")
    a_final = ((a_rec or {}).get("shadow") or {}).get("final_attempt_id")
    b_aid = ((b_rec or {}).get("shadow") or {}).get("final_attempt_id")
    shape = p39p_contamination_shape(
        request_b_id="req-B",
        request_a_attempt_id=a_aid,
        request_b_verified_attempt_id=b_vid,
        request_a_final_attempt_id=a_final,
        request_b_attempt_id=b_aid,
    )
    assert shape["contaminated"] is False
    assert b_vid != a_aid
    assert a_final != b_aid


def test_future_not_cancelled_worker_continues(tmp_path):
    """Timeout lifecycle: caller returns; worker is not cancelled."""
    cfg = _cfg(tmp_path, max_concurrency=1, timeout_sec=1.0)
    started = threading.Event()
    finished = threading.Event()

    def runner(snapshot, config=None):  # noqa: ANN001
        started.set()
        time.sleep(0.3)
        finished.set()
        return _sentinel_out(snapshot)

    set_force_runner_for_tests(runner)
    t0 = time.time()
    _schedule(cfg, "req-A", "PROMPT_A")
    assert started.wait(timeout=3)
    elapsed = time.time() - t0
    assert elapsed < 0.5  # caller did not wait for worker
    assert finished.wait(timeout=3)
    _drain()
    recs = _load_tel(tmp_path)
    _assert_bound(recs, "req-A", "PROMPT_A")


def test_route_multi_legacy_unchanged_when_shadow_off(monkeypatch):
    monkeypatch.delenv("MULTI_SHADOW_ENABLED", raising=False)
    out = route_multi_prompt(
        "요약해줘",
        named_frames=_frames(),
        base_url="http://x",
        model="m",
        profile_name=None,
        context_label=None,
        filter_df=None,
        request_id="req-A",
    )
    assert isinstance(out, SingleRouteOutcome)
    assert out.reply


def test_stress_zero_cross_request_contamination(tmp_path):
    iterations = 100
    contaminations = 0
    rng = random.Random(39)

    for i in range(iterations):
        reset_shadow_worker_for_tests()
        set_force_runner_for_tests(None)
        tel = tmp_path / f"iter_{i}"
        tel.mkdir()
        cfg_i = _cfg(tel, max_concurrency=3, queue_size=16)
        ids = [f"req-{ch}" for ch in "ABC"]
        prompts = {rid: f"PROMPT_{rid}" for rid in ids}
        releases = {rid: threading.Event() for rid in ids}
        started = {rid: threading.Event() for rid in ids}

        def runner(snapshot, config=None, _rel=releases, _st=started):  # noqa: ANN001
            _st[snapshot.request_id].set()
            if snapshot.request_id in _rel:
                _rel[snapshot.request_id].wait(timeout=5)
            if rng.random() < 0.1:
                raise RuntimeError("stress_exc")
            return _sentinel_out(snapshot)

        set_force_runner_for_tests(runner)
        for rid in ids:
            _schedule(cfg_i, rid, prompts[rid])
        for rid in ids:
            started[rid].wait(timeout=3)
        complete_order = list(ids)
        rng.shuffle(complete_order)
        for rid in complete_order:
            releases[rid].set()
        _drain()
        recs = _load_tel(tel)
        by_id = bind_records_by_request_id(recs)
        for rid in ids:
            rec = by_id.get(rid)
            if rec is None:
                contaminations += 1
                continue
            shadow = rec.get("shadow") or {}
            if rec.get("prompt") not in {prompts[rid], None} and rec.get("prompt") != prompts[rid]:
                contaminations += 1
                continue
            if rec.get("prompt") and rec.get("prompt") != prompts[rid]:
                contaminations += 1
                continue
            plan = shadow.get("final_plan") or {}
            if plan and plan.get("sentinel") and plan.get("sentinel") != f"PLAN_{rid}":
                contaminations += 1
                continue
            vid = shadow.get("verified_attempt_id")
            if vid and not str(vid).startswith(rid) and shadow.get("error_family") != "shadow_infrastructure_error":
                contaminations += 1
                continue
            report = lineage_integrity_report(
                request_id=rid,
                lineage=shadow.get("attempt_lineage"),
                verified_attempt_id=shadow.get("verified_attempt_id"),
                final_attempt_id=shadow.get("final_attempt_id"),
            )
            if shadow.get("attempt_lineage") and not report["ok"]:
                contaminations += 1

    assert contaminations == 0, f"cross_request_contamination={contaminations}/{iterations}"
