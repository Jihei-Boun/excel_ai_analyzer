"""Phase 34: frozen 7B V1 verifier generalization tests."""

from __future__ import annotations

import hashlib

from core.integrate.semantic_verifier import (
    VERIFIER_SYSTEM_PROMPT,
    assert_no_golden_leakage,
    build_verifier_payload,
)
from tests.benchmark_multi.phase34_generalization import (
    FROZEN_MODEL,
    FROZEN_VARIANT,
    _prompt_fingerprint,
    build_generalization_dataset,
)


def test_verifier_prompt_frozen_fingerprint_stable() -> None:
    fp = _prompt_fingerprint()
    assert fp["model"] == "qwen2.5:7b"
    assert fp["variant"] == "V1"
    assert fp["no_v2_v3"] is True
    h = hashlib.sha256(VERIFIER_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert fp["verifier_system_sha256"] == h


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
    assert "integration_plan" in payload
    assert "observed_result" not in payload
    assert "cross_file_understanding" not in payload
    assert_no_golden_leakage(payload)


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


def test_frozen_constants() -> None:
    assert FROZEN_MODEL == "qwen2.5:7b"
    assert FROZEN_VARIANT == "V1"
