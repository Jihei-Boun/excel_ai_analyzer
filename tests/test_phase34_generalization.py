"""Phase 34: frozen 7B V1 verifier generalization tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from core.integrate.semantic_verifier import (
    VERIFIER_SYSTEM_PROMPT,
    assert_no_golden_leakage,
    build_verifier_payload,
)
from tests.benchmark_multi.phase34_generalization import (
    CANONICAL_HISTORICAL_FIXTURE,
    FROZEN_MODEL,
    FROZEN_VARIANT,
    Phase34FixtureError,
    _prompt_fingerprint,
    build_generalization_dataset,
    load_canonical_historical_fixture,
)


def test_verifier_prompt_frozen_fingerprint_stable() -> None:
    fp = _prompt_fingerprint()
    assert fp["model"] == "qwen2.5:7b"
    assert fp["variant"] == "V1"
    assert fp["no_v2_v3"] is True
    h = hashlib.sha256(VERIFIER_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert fp["verifier_system_sha256"] == h


def test_canonical_historical_fixture_exists() -> None:
    assert CANONICAL_HISTORICAL_FIXTURE.is_file()
    valid, type_c = load_canonical_historical_fixture()
    assert len(valid) == 21
    assert len(type_c) == 9


def test_missing_canonical_fixture_fails_loudly(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(Phase34FixtureError):
        load_canonical_historical_fixture(missing)


def test_verifier_input_is_prompt_plus_plan_only() -> None:
    payload = build_verifier_payload(
        user_prompt="stack compatible rows",
        plan={
            "status": "planned",
            "steps": [
                {
                    "id": "1",
                    "op": "union_rows",
                    "inputs": ["a", "b"],
                    "output": "u",
                    "params": {},
                }
            ],
            "final_output": "u",
            "final_output_requirements": {
                "grain": "detail",
                "required_columns": ["id"],
            },
        },
        result={"columns": ["id"], "row_count": 2},
        understanding={"file_profiles": [{"source_id": "a"}]},
        variant=FROZEN_VARIANT,
    )
    assert "user_prompt" in payload
    # Phase 39B+: independent verifier splits plan_structure vs planner_claims
    # (legacy key integration_plan is no longer the default payload shape).
    assert "plan_structure" in payload or "integration_plan" in payload
    # Phase 39Z: V1 attaches bounded observed_result when a result is supplied.
    assert "observed_result" in payload
    assert payload["observed_result"]["row_count"] == 2
    assert "cross_file_understanding" not in payload
    assert_no_golden_leakage(payload)
    # Historical V1 with no result still omits the key (no CrossFileUnderstanding).
    payload_none = build_verifier_payload(
        user_prompt="stack compatible rows",
        plan={
            "status": "planned",
            "steps": [
                {
                    "id": "1",
                    "op": "union_rows",
                    "inputs": ["a", "b"],
                    "output": "u",
                    "params": {},
                }
            ],
            "final_output": "u",
        },
        result=None,
        variant=FROZEN_VARIANT,
    )
    assert "observed_result" not in payload_none
    assert "cross_file_understanding" not in payload_none


def test_dataset_has_no_scenario_routing_fields_in_verifier_path() -> None:
    ds = build_generalization_dataset()
    assert ds["counts"]["valid_count"] >= 20
    assert ds["counts"]["type_c_count"] >= 8
    # legitimate aggregates present for FP stress
    assert ds["diversity"]["legitimate_group_or_summary_aggregate"] >= 5
    for it in ds["items"][:3]:
        payload = build_verifier_payload(
            user_prompt=it["user_prompt"],
            plan=it["plan"],
            variant=FROZEN_VARIANT,
        )
        blob = str(payload)
        assert "TYPE_C" not in blob
        assert "overall_ok" not in blob
        assert "historical_real" not in blob


def test_dataset_ignores_live_benchmark_results_presence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Live SOURCES missing must not change canonical dataset counts."""
    import tests.benchmark_multi.phase34_generalization as m

    ds_with = build_generalization_dataset()
    monkeypatch.setattr(
        m,
        "LIVE_HARVEST_SOURCES",
        [tmp_path / "missing_a", tmp_path / "missing_b"],
    )
    monkeypatch.setattr(m, "SOURCES", m.LIVE_HARVEST_SOURCES)
    ds_without = build_generalization_dataset()
    assert ds_with["counts"] == ds_without["counts"]
    ids_a = [i["dataset_id"] for i in ds_with["items"]]
    ids_b = [i["dataset_id"] for i in ds_without["items"]]
    assert ids_a == ids_b


def test_frozen_constants() -> None:
    assert FROZEN_MODEL == "qwen2.5:7b"
    assert FROZEN_VARIANT == "V1"
