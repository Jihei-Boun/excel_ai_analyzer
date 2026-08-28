"""Phase 39L — verifier invocation capture instrumentation tests.

Observability only. Does not assert semantic answer correctness.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.integrate.semantic_verifier import (
    _normalize_verdict,
    run_semantic_verification,
)
from core.integrate.verifier_invocation_capture import (
    canonicalize_json,
    capture_enabled,
    classify_replay_fidelity,
    clear_last_record_for_tests,
    get_last_record_for_tests,
    persist_record,
    prompt_template_hash,
    sha256_text,
)


MIN_PLAN = {
    "steps": [
        {
            "op": "rename_columns",
            "params": {"mapping": {"a": "left_a"}},
            "inputs": ["f1.xlsx"],
            "output": "t1",
        }
    ]
}


def _mock_chat(verdict: str = "pass", reason: str = "satisfied"):
    def _fn(prompt, *, system, base_url, model):
        return {
            "verdict": verdict,
            "reason_code": reason,
            "evidence": ["mock"],
        }

    return _fn


@pytest.fixture(autouse=True)
def _clear_capture(monkeypatch, tmp_path):
    clear_last_record_for_tests()
    monkeypatch.delenv("MULTI_VERIFIER_CAPTURE_DIR", raising=False)
    monkeypatch.delenv("MULTI_VERIFIER_CAPTURE_ENABLED", raising=False)
    monkeypatch.delenv("MULTI_VERIFIER_CAPTURE_CASE_ID", raising=False)
    yield
    clear_last_record_for_tests()


def test_stable_hashing_same_payload():
    obj = {"b": 2, "a": 1, "nested": {"z": 0, "y": [1, 2]}}
    h1 = sha256_text(canonicalize_json(obj))
    h2 = sha256_text(canonicalize_json(obj))
    assert h1 == h2
    # Key order in Python dict construction must not affect canonical hash.
    obj2 = {"a": 1, "nested": {"y": [1, 2], "z": 0}, "b": 2}
    assert sha256_text(canonicalize_json(obj2)) == h1


def test_difference_detection_material_change():
    a = {"final_schema": ["x", "y"], "mode": "final_schema_expr_partition"}
    b = {"final_schema": ["x", "y", "z"], "mode": "final_schema_expr_partition"}
    assert sha256_text(canonicalize_json(a)) != sha256_text(canonicalize_json(b))


def test_canonicalization_only_sorts_keys():
    raw = canonicalize_json({"b": 1, "a": {"d": 2, "c": 3}})
    assert raw == '{"a":{"c":3,"d":2},"b":1}'


def test_prompt_template_hash_stable():
    h = prompt_template_hash("SYS", "PREFIX")
    assert h == prompt_template_hash("SYS", "PREFIX")
    assert h != prompt_template_hash("SYS", "PREFIX2")


def test_payload_capture_record(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_CASE_ID", "UNIT-CAP-1")
    assert capture_enabled() is True
    out = run_semantic_verification(
        user_prompt="compare left and right",
        plan=MIN_PLAN,
        result=None,
        variant="V2",
        chat_json_fn=_mock_chat("pass", "satisfied"),
        source_schemas={"f1.xlsx": ["a"]},
        materialization_mode="final_schema_expr_partition",
    )
    assert out.verdict == "pass"
    rec = get_last_record_for_tests()
    assert rec is not None
    assert rec["case_id"] == "UNIT-CAP-1"
    assert rec["exact_verifier_input"]["user"]
    assert rec["exact_payload_hash"]
    assert rec["canonical_payload_hash"]
    assert rec["raw_model_response_text"]
    assert rec["parsed_verdict"] == "pass"
    assert rec["parsed_reason_code"] == "satisfied"
    assert rec["temperature"] == 0.0
    assert rec["materialization_version"] == "final_schema_expr_partition"
    files = list(tmp_path.glob("verifier_invocations_*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    disk = json.loads(lines[0])
    assert disk["exact_payload_hash"] == rec["exact_payload_hash"]


def test_raw_response_preservation(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(tmp_path))

    def chat(prompt, *, system, base_url, model):
        return {
            "verdict": "fail",
            "reason_code": "wrong_output_grain",
            "evidence": ["claimed total_stock collapse"],
            "extra_model_field": "keep_me",
        }

    run_semantic_verification(
        user_prompt="stock vs use",
        plan=MIN_PLAN,
        chat_json_fn=chat,
        source_schemas={"f1.xlsx": ["a"]},
    )
    rec = get_last_record_for_tests()
    assert rec is not None
    raw_text = rec["raw_model_response_text"]
    assert "extra_model_field" in raw_text
    assert "keep_me" in raw_text
    assert rec["raw_model_response_parsed"]["extra_model_field"] == "keep_me"
    assert rec["parsed_reason_code"] == "wrong_output_grain"


def test_parser_trace_matches_normalize():
    raw = {
        "verdict": "FAIL",
        "reason_code": "wrong_output_grain",
        "evidence": ["x"],
    }
    norm = _normalize_verdict(raw)
    assert norm.verdict == "fail"
    assert norm.reason_code == "wrong_output_grain"
    # Unknown reason falls back to other — document, do not change.
    norm2 = _normalize_verdict({"verdict": "pass", "reason_code": "not_a_code"})
    assert norm2.reason_code == "other"


def test_telemetry_failure_does_not_alter_verdict(tmp_path, monkeypatch):
    # Capture enabled but persist path is a file (mkdir/write will fail).
    bad = tmp_path / "not_a_dir"
    bad.write_text("x", encoding="utf-8")
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(bad))
    out = run_semantic_verification(
        user_prompt="q",
        plan=MIN_PLAN,
        chat_json_fn=_mock_chat("uncertain", "insufficient_evidence"),
        source_schemas={"f1.xlsx": ["a"]},
    )
    assert out.verdict == "uncertain"
    assert out.reason_code == "insufficient_evidence"


def test_capture_off_by_default_no_side_effect(monkeypatch):
    monkeypatch.delenv("MULTI_VERIFIER_CAPTURE_DIR", raising=False)
    assert capture_enabled() is False
    out = run_semantic_verification(
        user_prompt="q",
        plan=MIN_PLAN,
        chat_json_fn=_mock_chat(),
        source_schemas={"f1.xlsx": ["a"]},
    )
    assert out.verdict == "pass"
    # Without enable, last record may still be unset.
    assert get_last_record_for_tests() is None


def test_shadow_isolation_instrumentation_not_user_facing(tmp_path, monkeypatch):
    """Capture must not invent a user-facing answer field."""
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(tmp_path))
    out = run_semantic_verification(
        user_prompt="q",
        plan=MIN_PLAN,
        chat_json_fn=_mock_chat("fail", "wrong_output_grain"),
        source_schemas={"f1.xlsx": ["a"]},
    )
    d = out.to_dict() if hasattr(out, "to_dict") else out.__dict__
    assert "user_answer" not in d
    assert "legacy_response" not in d
    assert out.verdict == "fail"


def test_replay_fidelity_classifier():
    assert (
        classify_replay_fidelity(
            source_exact_payload_hash="aaa",
            replay_exact_payload_hash="aaa",
            source_canonical_payload_hash="c1",
            replay_canonical_payload_hash="c1",
            used_captured_verbatim_user=True,
            reconstructed_from_plan=False,
        )
        == "EXACT_REPLAY"
    )
    assert (
        classify_replay_fidelity(
            source_exact_payload_hash="aaa",
            replay_exact_payload_hash="bbb",
            source_canonical_payload_hash="c1",
            replay_canonical_payload_hash="c1",
            used_captured_verbatim_user=True,
            reconstructed_from_plan=False,
        )
        == "CANONICAL_EQUIVALENT_REPLAY"
    )
    assert (
        classify_replay_fidelity(
            source_exact_payload_hash=None,
            replay_exact_payload_hash="x",
            source_canonical_payload_hash=None,
            replay_canonical_payload_hash="y",
            used_captured_verbatim_user=False,
            reconstructed_from_plan=True,
        )
        == "RECONSTRUCTED_REPLAY"
    )


def test_persist_record_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTI_VERIFIER_CAPTURE_DIR", str(tmp_path / "ok"))
    path = persist_record({"hello": 1, "exact_payload_hash": "x"})
    assert path is not None
    assert path.exists()
