"""Phase 38 blocker fix — Shadow Integration validation contract mapping."""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from core.integrate.integration_execution_types import IntegrationExecutionResult
from core.integrate.integration_pipeline import IntegrationPipelineResult
from core.integrate.integration_result_validation_types import (
    IntegrationResultValidationIssue,
    IntegrationResultValidationResult,
)
from core.integrate.integration_validation_types import (
    IntegrationValidationIssue,
    IntegrationValidationResult,
)
from core.routing.route_types import SingleRouteOutcome
from core.shadow.config import ShadowConfig
from core.shadow.hook import finish_with_shadow
from core.shadow.runner import map_integration_result_telemetry, run_shadow_pipeline
from core.shadow.snapshot import build_shadow_snapshot
from core.shadow.worker import (
    reset_shadow_worker_for_tests,
    set_force_runner_for_tests,
)

RUNNER_PATH = Path(__file__).resolve().parents[1] / "core" / "shadow" / "runner.py"


@pytest.fixture(autouse=True)
def _reset_shadow():
    reset_shadow_worker_for_tests()
    set_force_runner_for_tests(None)
    yield
    reset_shadow_worker_for_tests()
    set_force_runner_for_tests(None)


def _pipeline(
    *,
    status: str = "success",
    plan_valid: bool | None = True,
    exec_success: bool | None = True,
    result_valid: bool | None = True,
) -> IntegrationPipelineResult:
    plan_val = None
    if plan_valid is not None:
        errs = []
        if not plan_valid:
            errs = [
                IntegrationValidationIssue(
                    code="test_invalid",
                    severity="error",
                    message="fixture invalid plan",
                )
            ]
        plan_val = IntegrationValidationResult(valid=plan_valid, errors=errs)

    execution = None
    if exec_success is not None:
        execution = IntegrationExecutionResult(success=exec_success)

    result_val = None
    if result_valid is not None:
        r_errs = []
        if not result_valid:
            r_errs = [
                IntegrationResultValidationIssue(
                    code="test_result_invalid",
                    severity="error",
                    message="fixture invalid result",
                )
            ]
        result_val = IntegrationResultValidationResult(
            valid=result_valid, errors=r_errs
        )

    return IntegrationPipelineResult(
        status=status,
        plan=None,
        plan_validation=plan_val,
        execution=execution,
        result_validation=result_val,
        final_output=pd.DataFrame({"a": [1]}) if status == "success" else None,
        metadata={"final_path": "fast_success"},
    )


def test_plan_validation_valid_true_maps_ok() -> None:
    tel = map_integration_result_telemetry(_pipeline(plan_valid=True))
    assert tel["plan_validation_status"] == "ok"
    assert tel["shadow_success"] is True


def test_plan_validation_valid_false_maps_failed() -> None:
    tel = map_integration_result_telemetry(
        _pipeline(status="failed", plan_valid=False, exec_success=None, result_valid=None)
    )
    assert tel["plan_validation_status"] == "failed"
    assert tel["plan_validation_codes"] == ["test_invalid"]
    assert tel["shadow_success"] is False


def test_execution_success_mapping() -> None:
    tel_ok = map_integration_result_telemetry(_pipeline(exec_success=True))
    assert tel_ok["executor_status"] is True
    tel_bad = map_integration_result_telemetry(_pipeline(exec_success=False))
    assert tel_bad["executor_status"] is False


def test_result_validation_valid_mapping() -> None:
    tel_ok = map_integration_result_telemetry(_pipeline(result_valid=True))
    assert tel_ok["result_validation_status"] is True
    tel_bad = map_integration_result_telemetry(_pipeline(result_valid=False))
    assert tel_bad["result_validation_status"] is False


def test_none_plan_validation_treated_as_ok() -> None:
    tel = map_integration_result_telemetry(_pipeline(plan_valid=None))
    assert tel["plan_validation_status"] == "ok"


def test_adapter_does_not_use_legacy_ok_on_integration_types() -> None:
    """Static: map_integration_result_telemetry / nearby mapping must not use .ok."""
    src = RUNNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Collect Attribute loads of .ok inside map_integration_result_telemetry
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "map_integration_result_telemetry":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and sub.attr == "ok":
                    pytest.fail(
                        "map_integration_result_telemetry must not access .ok "
                        "(use Integration .valid / .success)"
                    )
            break
    else:
        pytest.fail("map_integration_result_telemetry not found")

    # Also ensure Integration types themselves have no .ok
    assert not hasattr(IntegrationValidationResult(valid=True), "ok")
    assert not hasattr(IntegrationResultValidationResult(valid=True), "ok")
    assert not hasattr(IntegrationExecutionResult(success=True), "ok")
    assert hasattr(IntegrationValidationResult(valid=True), "valid")
    assert hasattr(IntegrationExecutionResult(success=True), "success")


def test_run_shadow_pipeline_stubbed_no_attribute_error(monkeypatch, tmp_path) -> None:
    """Smoke: full run_shadow_pipeline path with real Integration objects, no LLM."""
    fake = _pipeline(plan_valid=True, exec_success=True, result_valid=True)

    monkeypatch.setattr(
        "core.shadow.runner.build_cross_file_understanding",
        lambda *a, **k: {"status": "stub"},
    )
    monkeypatch.setattr(
        "core.shadow.runner.run_integration_pipeline_semantic_experimental",
        lambda *a, **k: fake,
    )

    snap = build_shadow_snapshot(
        prompt="join amounts by project",
        named_frames=[
            ("expenses", pd.DataFrame({"project_id": [1], "amount": [10]})),
            ("projects", pd.DataFrame({"project_id": [1], "name": ["A"]})),
        ],
        base_url="http://localhost:11434",
        model="qwen2.5:7b",
    )
    cfg = ShadowConfig(enabled=True, telemetry_dir=tmp_path, timeout_sec=600.0)
    out = run_shadow_pipeline(snap, config=cfg)
    assert out["shadow_completed"] is True
    assert out.get("error_family") != "shadow_pipeline_exception"
    assert "AttributeError" not in (out.get("error_message") or "")
    assert out["plan_validation_status"] == "ok"
    assert out["executor_status"] is True
    assert out["result_validation_status"] is True
    assert out["shadow_success"] is True
    assert out["shadow_status"] == "success"


def test_run_shadow_pipeline_stubbed_invalid_plan(monkeypatch, tmp_path) -> None:
    fake = _pipeline(
        status="failed", plan_valid=False, exec_success=None, result_valid=None
    )
    monkeypatch.setattr(
        "core.shadow.runner.build_cross_file_understanding",
        lambda *a, **k: {"status": "stub"},
    )
    monkeypatch.setattr(
        "core.shadow.runner.run_integration_pipeline_semantic_experimental",
        lambda *a, **k: fake,
    )
    snap = build_shadow_snapshot(
        prompt="x",
        named_frames=[
            ("a", pd.DataFrame({"id": [1]})),
            ("b", pd.DataFrame({"id": [1]})),
        ],
        base_url="http://x",
        model="m",
    )
    out = run_shadow_pipeline(
        snap, config=ShadowConfig(enabled=True, telemetry_dir=tmp_path)
    )
    assert out["plan_validation_status"] == "failed"
    assert out["shadow_success"] is False
    assert "AttributeError" not in (out.get("error_message") or "")


def test_shadow_exception_does_not_change_legacy(tmp_path) -> None:
    cfg = ShadowConfig(
        enabled=True,
        telemetry_dir=tmp_path,
        inline_for_tests=True,
    )

    def boom(snapshot, config=None):  # noqa: ANN001
        raise RuntimeError("shadow boom")

    set_force_runner_for_tests(boom)
    snap = build_shadow_snapshot(
        prompt="join",
        named_frames=[
            ("a", pd.DataFrame({"id": [1]})),
            ("b", pd.DataFrame({"id": [1]})),
        ],
        base_url="http://x",
        model="m",
    )
    legacy = SingleRouteOutcome(
        reply="legacy-ok",
        dataframe=pd.DataFrame({"id": [1]}),
        operation_name="structured_integrate",
    )
    returned = finish_with_shadow(legacy, snapshot=snap, config=cfg)
    assert returned is legacy
    assert returned.reply == "legacy-ok"
