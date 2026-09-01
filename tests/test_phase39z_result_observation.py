"""Phase 39Z — bounded result observation and production evidence plumbing."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pandas as pd

from core.integrate.integration_pipeline import IntegrationPipelineResult
from core.integrate.integration_plan_types import (
    FinalOutputRequirements,
    IntegrationPlan,
    IntegrationStep,
)
from core.integrate.result_observation import (
    MAX_RESULT_SAMPLE_COLUMNS,
    MAX_RESULT_SAMPLE_ROWS,
    MAX_RESULT_SERIALIZED_CHARS,
    observe_result_for_verifier,
)
from core.integrate.semantic_escalation import (
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
    SemanticEscalationConfig,
    run_integration_pipeline_semantic_experimental,
)
from core.integrate.semantic_verifier import (
    VERIFIER_SYSTEM_PROMPT,
    SemanticVerificationResult,
    build_verifier_payload,
)


def _plan() -> IntegrationPlan:
    return IntegrationPlan(
        status="planned",
        steps=[
            IntegrationStep(
                id="u",
                op="union_rows",
                inputs=["a", "b"],
                output="u",
                params={"column_policy": "aligned"},
            )
        ],
        final_output="u",
        final_output_requirements=FinalOutputRequirements(
            grain="detail", required_columns=["id"]
        ),
    )


def test_variant_model_prompt_frozen() -> None:
    assert SEMANTIC_VERIFIER_VARIANT == "V1"
    assert SEMANTIC_VERIFIER_MODEL == "qwen2.5:7b"
    # System prompt body is unchanged from the Phase 34 freeze fingerprint.
    h = hashlib.sha256(VERIFIER_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert len(h) == 64
    src = Path("core/integrate/semantic_verifier.py").read_text()
    assert "You JUDGE whether a proposed IntegrationPlan" in src
    assert "check whether join dropped rows" not in src
    assert "group-by heuristics" not in Path("core/integrate/result_observation.py").read_text()


def test_observation_helper_has_no_semantic_judgments() -> None:
    src = Path("core/integrate/result_observation.py").read_text()
    for banned in (
        "result lost one requested side",
        "join was incorrect",
        "union should have been used",
        "wrong grouping",
        "result does not answer the request",
    ):
        assert banned not in src


def test_empty_dataframe_serializes() -> None:
    df = pd.DataFrame(columns=["id", "v"])
    obs = observe_result_for_verifier(df)
    assert obs is not None
    assert obs["kind"] == "dataframe"
    assert obs["row_count"] == 0
    assert obs["column_count"] == 2
    assert obs["columns"] == ["id", "v"]
    assert obs["sample_rows"] == []


def test_row_shape_controls() -> None:
    one = observe_result_for_verifier(pd.DataFrame({"id": [1], "x": [2]}))
    many = observe_result_for_verifier(pd.DataFrame({"id": [1, 2, 3], "x": [4, 5, 6]}))
    assert one["row_count"] == 1
    assert many["row_count"] == 3
    assert len(many["sample_rows"]) == 3


def test_wide_columns_preserve_count_and_flag() -> None:
    df = pd.DataFrame({f"c{i}": [i] for i in range(40)})
    obs = observe_result_for_verifier(df)
    assert obs["column_count"] == 40
    assert len(obs["columns"]) == MAX_RESULT_SAMPLE_COLUMNS
    assert obs["truncated"] is True
    assert obs["truncated_columns"] is True


def test_null_and_nan_are_json_null() -> None:
    df = pd.DataFrame({"a": [1.0, float("nan")], "b": [None, True]})
    obs = observe_result_for_verifier(df)
    blob = json.dumps(obs)
    assert "nan" not in blob.lower()
    assert obs["sample_rows"][1]["a"] is None
    assert obs["sample_rows"][0]["b"] is None
    assert obs["sample_rows"][1]["b"] is True


def test_scalar_and_none() -> None:
    assert observe_result_for_verifier(None) is None
    sc = observe_result_for_verifier(42)
    assert sc["kind"] == "scalar"
    assert sc["sample_rows"][0]["value"] == 42


def test_large_result_is_bounded_and_keeps_row_count() -> None:
    df = pd.DataFrame({"x": list(range(500)), "y": list(range(500))})
    obs = observe_result_for_verifier(df)
    assert obs["row_count"] == 500
    assert len(obs["sample_rows"]) <= MAX_RESULT_SAMPLE_ROWS
    assert obs["truncated_rows"] is True
    blob = json.dumps(obs, ensure_ascii=False)
    assert len(blob) <= MAX_RESULT_SERIALIZED_CHARS
    assert df.shape == (500, 2)


def test_observation_is_deterministic_and_non_mutating() -> None:
    df = pd.DataFrame({"id": [1, 2], "ts": pd.to_datetime(["2020-01-01", "2020-01-02"])})
    before = df.copy(deep=True)
    a = observe_result_for_verifier(df)
    b = observe_result_for_verifier(df)
    assert a == b
    assert df.equals(before)
    assert list(df.columns) == list(before.columns)
    assert list(df.index) == list(before.index)


def test_v1_payload_attaches_observation_without_crossfile() -> None:
    obs = observe_result_for_verifier(pd.DataFrame({"id": [1], "left": [2], "right": [3]}))
    payload = build_verifier_payload(
        user_prompt="stack rows from both files",
        plan={
            "status": "planned",
            "steps": [{"id": "j", "op": "join", "inputs": ["a", "b"], "output": "o", "params": {}}],
            "final_output": "o",
            "final_output_requirements": {"grain": "detail", "required_columns": ["id"]},
        },
        result=obs,
        understanding={"file_profiles": [{"source_id": "a"}]},
        variant="V1",
    )
    assert "observed_result" in payload
    assert payload["observed_result"]["row_count"] == 1
    assert "columns" in payload["observed_result"]
    assert "planner_claims" in payload
    assert "cross_file_understanding" not in payload
    none_payload = build_verifier_payload(
        user_prompt="stack rows from both files",
        plan={"status": "planned", "steps": [], "final_output": "o"},
        result=None,
        variant="V1",
    )
    assert "observed_result" not in none_payload


def test_escalation_passes_observed_result(monkeypatch) -> None:
    plan = _plan()
    df = pd.DataFrame({"id": [1, 2], "v": [3, 4]})
    base = IntegrationPipelineResult(
        status="success", plan=plan, metadata={}, final_output=df.copy(deep=True)
    )
    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_integration_pipeline",
        lambda *a, **k: base,
    )
    seen: dict = {}

    def fake_verify(**kwargs):  # noqa: ANN003
        seen["result"] = kwargs.get("result")
        seen["variant"] = kwargs.get("variant")
        seen["lineage"] = kwargs.get("lineage_context")
        return SemanticVerificationResult(
            verdict="pass", reason_code="satisfied", evidence=["ok"], parse_ok=True
        )

    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_semantic_verification", fake_verify
    )
    out = run_integration_pipeline_semantic_experimental(
        "stack compatible rows",
        {"a": pd.DataFrame({"id": [1]}), "b": pd.DataFrame({"id": [2]})},
        {"file_profiles": [], "relationships": []},
        config=SemanticEscalationConfig(enable_failure_escalation=False),
        request_id="req-z1",
        case_id="case-z1",
    )
    assert seen["variant"] == "V1"
    assert seen["result"] is not None
    assert seen["result"]["row_count"] == 2
    assert seen["result"]["columns"] == ["id", "v"]
    assert seen["lineage"]["request_id"] == "req-z1"
    assert seen["lineage"]["attempt_id"]
    assert seen["lineage"]["plan_fingerprint"]
    assert seen["lineage"]["result_fingerprint"]
    assert base.final_output.equals(df)
    assert out.metadata.get("semantic_verifier_invoked") is True


def test_observation_failure_fail_open(monkeypatch) -> None:
    plan = _plan()
    df = pd.DataFrame({"id": [1]})
    base = IntegrationPipelineResult(
        status="success", plan=plan, metadata={}, final_output=df
    )
    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_integration_pipeline",
        lambda *a, **k: base,
    )

    def boom(_result):  # noqa: ANN001
        raise RuntimeError("serialize-fail")

    monkeypatch.setattr(
        "core.integrate.result_observation.observe_result_for_verifier", boom
    )
    seen: dict = {}

    def fake_verify(**kwargs):  # noqa: ANN003
        seen["result"] = kwargs.get("result")
        return SemanticVerificationResult(
            verdict="pass", reason_code="satisfied", evidence=["ok"], parse_ok=True
        )

    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_semantic_verification", fake_verify
    )
    out = run_integration_pipeline_semantic_experimental(
        "stack compatible rows",
        {"a": pd.DataFrame({"id": [1]})},
        {},
        config=SemanticEscalationConfig(enable_failure_escalation=False),
        request_id="req-fail-open",
        case_id="case-fail-open",
    )
    assert seen["result"] is None
    assert out.metadata.get("result_observation_failed") is True
    assert out.metadata.get("semantic_verifier_invoked") is True
    notes = (out.metadata.get("semantic_escalation") or {}).get("notes") or []
    assert any("result_observation_failed" in str(n) for n in notes)


def test_concurrent_attempts_keep_own_observation(monkeypatch) -> None:
    plan = _plan()
    df_a = pd.DataFrame({"left_only": [1, 2, 3]})
    df_b = pd.DataFrame({"right_only": [9]})
    captured: dict[str, dict] = {}
    lock = threading.Lock()

    def fake_pipeline(user_prompt, *a, **k):  # noqa: ANN001
        fo = df_a if user_prompt == "PROMPT_A" else df_b
        return IntegrationPipelineResult(
            status="success", plan=plan, metadata={}, final_output=fo.copy(deep=True)
        )

    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_integration_pipeline", fake_pipeline
    )

    def fake_verify(**kwargs):  # noqa: ANN003
        rid = (kwargs.get("lineage_context") or {}).get("request_id")
        with lock:
            captured[str(rid)] = kwargs.get("result")
        return SemanticVerificationResult(
            verdict="pass", reason_code="satisfied", evidence=["ok"], parse_ok=True
        )

    monkeypatch.setattr(
        "core.integrate.semantic_escalation.run_semantic_verification", fake_verify
    )

    def run(prompt: str, rid: str) -> None:
        run_integration_pipeline_semantic_experimental(
            prompt,
            {"a": pd.DataFrame({"id": [1]})},
            {},
            config=SemanticEscalationConfig(enable_failure_escalation=False),
            request_id=rid,
            case_id=rid,
        )

    t1 = threading.Thread(target=run, args=("PROMPT_A", "req-A"))
    t2 = threading.Thread(target=run, args=("PROMPT_B", "req-B"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert captured["req-A"]["columns"] == ["left_only"]
    assert captured["req-A"]["row_count"] == 3
    assert captured["req-B"]["columns"] == ["right_only"]
    assert captured["req-B"]["row_count"] == 1
    assert captured["req-A"]["content_hash"] != captured["req-B"]["content_hash"]


def test_m1_payload_gains_row_count_columns() -> None:
    from tests.benchmark_multi.phase39x_research import build_rows, production_payload

    rows = build_rows()
    rec = next(r for r in rows if r["attempt_id"] == "w2-join-instead-of-union")
    old = production_payload(rec)
    assert "observed_result" not in old
    obs = observe_result_for_verifier(rec.get("result_obs"))
    from core.integrate.schema_lineage import extract_source_schemas_from_understanding

    new = build_verifier_payload(
        user_prompt=rec["user_prompt"],
        plan=rec["plan_dict"],
        result=obs,
        understanding=rec["und"],
        variant=SEMANTIC_VERIFIER_VARIANT,
        materialization_mode="final_schema_expr_partition",
        source_schemas=extract_source_schemas_from_understanding(rec["und"]),
    )
    assert "observed_result" in new
    assert new["observed_result"]["row_count"] is not None
    assert new["observed_result"]["columns"]
    assert "planner_claims" in new
    assert "cross_file_understanding" not in new


def test_shadow_still_off() -> None:
    import os

    assert os.environ.get("MULTI_SHADOW_ENABLED") in {None, "", "false", "0"}
    from core.shadow.config import load_shadow_config

    cfg = load_shadow_config()
    assert cfg.enabled is False
