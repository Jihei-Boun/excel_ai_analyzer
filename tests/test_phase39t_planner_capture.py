"""Phase 39T — planner capture is observational and default OFF."""

from __future__ import annotations

from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.integration_planner import build_integration_plan
from core.integrate.planner_invocation_capture import (
    canonicalize_json,
    capture_enabled,
    clear_last_record_for_tests,
    get_last_record_for_tests,
    sha256_text,
)


def _und():
    return {
        "file_profiles": [
            {
                "source_id": "a.xlsx",
                "row_count": 2,
                "column_count": 2,
                "observations": {
                    "columns": [
                        {"name": "id", "dtype_family": "string", "sample_values": ["1"]},
                        {"name": "v", "dtype_family": "numeric", "sample_values": [1]},
                    ],
                    "column_names": ["id", "v"],
                },
            },
            {
                "source_id": "b.xlsx",
                "row_count": 2,
                "column_count": 2,
                "observations": {
                    "columns": [
                        {"name": "id", "dtype_family": "string", "sample_values": ["1"]},
                        {"name": "w", "dtype_family": "numeric", "sample_values": [2]},
                    ],
                    "column_names": ["id", "w"],
                },
            },
        ],
        "pairwise_observations": [],
        "relationships": [],
    }


def _plan_dict():
    return {
        "status": "planned",
        "steps": [
            {
                "id": "j",
                "op": "join",
                "inputs": ["a.xlsx", "b.xlsx"],
                "output": "out",
                "params": {
                    "left_keys": ["id"],
                    "right_keys": ["id"],
                    "how": "inner",
                },
            }
        ],
        "final_output": "out",
        "final_output_requirements": {
            "grain": "entity",
            "required_columns": ["id", "v", "w"],
        },
    }


def test_capture_default_off(monkeypatch):
    monkeypatch.delenv("MULTI_PLANNER_CAPTURE_ENABLED", raising=False)
    monkeypatch.delenv("MULTI_PLANNER_CAPTURE_DIR", raising=False)
    assert capture_enabled() is False


def test_capture_does_not_change_plan(monkeypatch, tmp_path):
    clear_last_record_for_tests()
    monkeypatch.setenv("MULTI_PLANNER_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("MULTI_PLANNER_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("MULTI_PLANNER_CAPTURE_REQUEST_ID", "p39t-test-1")

    def chat(_prompt, *, system, base_url, model):
        return _plan_dict()

    und = _und()
    a = build_integration_plan("join them", und, chat_json_fn=chat, model="qwen2.5:7b")
    rec = get_last_record_for_tests()
    assert rec is not None
    assert rec["request_id"] == "p39t-test-1"
    assert rec["parse_ok"] is True
    b = integration_plan_from_dict(_plan_dict())
    assert a.status == b.status
    assert [s.op for s in a.steps] == [s.op for s in b.steps]


def test_capture_off_same_plan(monkeypatch):
    clear_last_record_for_tests()
    monkeypatch.delenv("MULTI_PLANNER_CAPTURE_ENABLED", raising=False)
    monkeypatch.delenv("MULTI_PLANNER_CAPTURE_DIR", raising=False)

    def chat(_prompt, *, system, base_url, model):
        return _plan_dict()

    p = build_integration_plan("join them", _und(), chat_json_fn=chat)
    assert p.status == "planned"
    assert get_last_record_for_tests() is None


def test_hash_stable():
    h1 = sha256_text(canonicalize_json({"b": 1, "a": 2}))
    h2 = sha256_text(canonicalize_json({"a": 2, "b": 1}))
    assert h1 == h2
