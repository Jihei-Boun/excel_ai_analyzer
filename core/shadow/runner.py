"""Shadow candidate runner — frozen Phase 35 semantic escalation path."""

from __future__ import annotations

import time
import traceback
from typing import Any, Callable

import pandas as pd

from core.integrate.relationship_infer import build_cross_file_understanding
from core.integrate.semantic_escalation import (
    SemanticEscalationConfig,
    run_integration_pipeline_semantic_experimental,
)
from core.shadow.config import ShadowConfig
from core.shadow.fingerprint import dataframe_fingerprint
from core.shadow.snapshot import ShadowRequestSnapshot


def _safe_plan_dict(plan: Any) -> dict[str, Any] | None:
    if plan is None:
        return None
    if hasattr(plan, "to_dict"):
        return plan.to_dict()
    if isinstance(plan, dict):
        return plan
    return None


def run_shadow_pipeline(
    snapshot: ShadowRequestSnapshot,
    *,
    config: ShadowConfig,
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    inject_error: str | None = None,
) -> dict[str, Any]:
    """Execute frozen candidate. Returns telemetry payload (never raises)."""
    t0 = time.time()
    out: dict[str, Any] = {
        "shadow_started": True,
        "shadow_completed": False,
        "shadow_status": "running",
        "error_family": None,
        "error_message": None,
        "latency_total_s": None,
        "latency_by_stage_s": {},
    }
    if inject_error:
        out["shadow_status"] = "shadow_infrastructure_error"
        out["error_family"] = "shadow_infrastructure_error"
        out["error_message"] = inject_error
        out["shadow_completed"] = True
        out["latency_total_s"] = round(time.time() - t0, 3)
        return out

    if sleep_fn is not None:
        # Test hook for latency isolation (does not call models)
        sleep_fn(0)
    try:
        named = list(snapshot.sources.items())
        t_und = time.time()
        understanding = build_cross_file_understanding(
            named,
            base_url=snapshot.base_url,
            model="qwen2.5:7b",
            chat_json_fn=chat_json_fn,
            infer_relationships=True,
        )
        out["latency_by_stage_s"]["understanding"] = round(time.time() - t_und, 3)
        out["understanding_status"] = "ok"

        t_pipe = time.time()
        result = run_integration_pipeline_semantic_experimental(
            snapshot.prompt_for_pipeline(),
            snapshot.sources,
            understanding,
            config=SemanticEscalationConfig(
                enable_failure_escalation=True,
                enable_semantic_escalation=True,
                uncertain_policy="escalate",
            ),
            base_url=snapshot.base_url,
            chat_json_fn=chat_json_fn,
        )
        out["latency_by_stage_s"]["pipeline"] = round(time.time() - t_pipe, 3)

        meta = dict(result.metadata or {})
        out["shadow_status"] = result.status
        out["planner_status"] = result.status
        out["plan_validation_status"] = (
            "ok"
            if result.plan_validation is None or result.plan_validation.ok
            else "failed"
        )
        if result.plan_validation is not None and hasattr(
            result.plan_validation, "errors"
        ):
            out["plan_validation_codes"] = [
                getattr(e, "code", str(e)) for e in (result.plan_validation.errors or [])
            ]
        out["executor_status"] = (
            None if result.execution is None else getattr(result.execution, "ok", None)
        )
        out["result_validation_status"] = (
            None
            if result.result_validation is None
            else getattr(result.result_validation, "ok", None)
        )
        out["cannot_plan"] = result.status == "cannot_plan"
        out["retry_exhausted"] = bool(meta.get("fast_path_status") == "retry_exhausted")
        out["fast_attempt_count"] = meta.get("fast_attempt_count")
        out["fast_retry_count"] = meta.get("fast_retry_count")
        out["semantic_verifier_invoked"] = bool(meta.get("semantic_verifier_invoked"))
        ver = meta.get("semantic_verifier") or {}
        out["semantic_verifier_verdict"] = ver.get("verdict")
        out["semantic_verifier_reason"] = ver.get("reason_code")
        out["semantic_verifier_evidence"] = ver.get("evidence")
        out["failure_32b_invoked"] = bool(meta.get("failure_escalation_32b"))
        out["semantic_32b_invoked"] = bool(meta.get("semantic_escalation_32b"))
        out["total_32b_calls"] = int(out["failure_32b_invoked"]) + int(
            out["semantic_32b_invoked"]
        )
        out["final_path"] = meta.get("final_path")
        out["escalation_source"] = meta.get("escalation_source")
        out["semantic_verifier_elapsed_s"] = meta.get("semantic_verifier_elapsed_s")
        out["semantic_strong_elapsed_s"] = meta.get("semantic_strong_elapsed_s")
        out["model_calls"] = _model_calls_from_meta(meta)
        out["final_plan"] = _safe_plan_dict(result.plan)
        out["result_fingerprint"] = dataframe_fingerprint(result.final_output)
        out["shadow_success"] = result.status == "success"
        out["shadow_completed"] = True
    except Exception as exc:  # noqa: BLE001
        out["shadow_status"] = "shadow_pipeline_exception"
        out["error_family"] = "shadow_pipeline_exception"
        out["error_message"] = f"{type(exc).__name__}: {exc}"
        out["error_traceback_tail"] = traceback.format_exc()[-1500:]
        out["shadow_success"] = False
        out["shadow_completed"] = True
    out["latency_total_s"] = round(time.time() - t0, 3)
    # Operational timeout marking (caller may also enforce)
    if out["latency_total_s"] is not None and out["latency_total_s"] > config.timeout_sec:
        out["error_family"] = out.get("error_family") or "shadow_timeout"
        out["shadow_timeout"] = True
    return out


def _model_calls_from_meta(meta: dict[str, Any]) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []
    calls.append({"model_name": "qwen2.5:7b", "purpose": "understanding"})
    calls.append({"model_name": "qwen2.5:7b", "purpose": "fast_planner"})
    if meta.get("semantic_verifier_invoked"):
        calls.append({"model_name": "qwen2.5:7b", "purpose": "semantic_verifier"})
    if meta.get("failure_escalation_32b"):
        calls.append({"model_name": "qwen3:32b", "purpose": "failure_escalation"})
    if meta.get("semantic_escalation_32b"):
        calls.append({"model_name": "qwen3:32b", "purpose": "semantic_escalation"})
    return calls


def run_shadow_sleep_only(seconds: float) -> dict[str, Any]:
    """Test helper: slow shadow without LLM."""
    t0 = time.time()
    time.sleep(seconds)
    return {
        "shadow_started": True,
        "shadow_completed": True,
        "shadow_status": "test_sleep",
        "shadow_success": False,
        "latency_total_s": round(time.time() - t0, 3),
        "error_family": None,
    }
