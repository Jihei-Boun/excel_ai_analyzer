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


def map_integration_result_telemetry(result: Any) -> dict[str, Any]:
    """Map IntegrationPipelineResult → Shadow telemetry fields.

    Uses Integration contracts only (no legacy ValidationReport.ok):
      plan_validation     → .valid
      execution           → .success
      result_validation   → .valid
    """
    meta = dict(getattr(result, "metadata", None) or {})
    plan_val = getattr(result, "plan_validation", None)
    execution = getattr(result, "execution", None)
    result_val = getattr(result, "result_validation", None)

    if plan_val is None:
        plan_validation_status = "ok"
    else:
        plan_validation_status = "ok" if bool(plan_val.valid) else "failed"

    plan_validation_codes: list[str] | None = None
    if plan_val is not None:
        plan_validation_codes = [
            getattr(e, "code", str(e)) for e in (plan_val.errors or [])
        ]

    executor_status = None if execution is None else bool(execution.success)

    if result_val is None:
        result_validation_status = None
    else:
        result_validation_status = bool(result_val.valid)

    ver = meta.get("semantic_verifier") or {}
    out: dict[str, Any] = {
        "shadow_status": getattr(result, "status", None),
        "planner_status": getattr(result, "status", None),
        "plan_validation_status": plan_validation_status,
        "plan_validation_codes": plan_validation_codes,
        "executor_status": executor_status,
        "result_validation_status": result_validation_status,
        "cannot_plan": getattr(result, "status", None) == "cannot_plan",
        "retry_exhausted": bool(meta.get("fast_path_status") == "retry_exhausted"),
        "fast_attempt_count": meta.get("fast_attempt_count"),
        "fast_retry_count": meta.get("fast_retry_count"),
        "semantic_verifier_invoked": bool(meta.get("semantic_verifier_invoked")),
        "semantic_verifier_verdict": ver.get("verdict"),
        "semantic_verifier_reason": ver.get("reason_code"),
        "semantic_verifier_evidence": ver.get("evidence"),
        "failure_32b_invoked": bool(meta.get("failure_escalation_32b")),
        "semantic_32b_invoked": bool(meta.get("semantic_escalation_32b")),
        "final_path": meta.get("final_path"),
        "escalation_source": meta.get("escalation_source"),
        "semantic_verifier_elapsed_s": meta.get("semantic_verifier_elapsed_s"),
        "semantic_strong_elapsed_s": meta.get("semantic_strong_elapsed_s"),
        "model_calls": _model_calls_from_meta(meta),
        "final_plan": _safe_plan_dict(getattr(result, "plan", None)),
        "result_fingerprint": dataframe_fingerprint(
            getattr(result, "final_output", None)
        ),
        "shadow_success": getattr(result, "status", None) == "success",
        # Phase 39O attempt lineage (null-safe for historical records)
        "attempt_lineage": meta.get("attempt_lineage"),
        "verified_attempt_id": meta.get("verified_attempt_id"),
        "final_attempt_id": meta.get("final_attempt_id"),
        "verified_plan_fingerprint": meta.get("verified_plan_fingerprint"),
        "final_plan_fingerprint": meta.get("final_plan_fingerprint"),
    }
    out["total_32b_calls"] = int(out["failure_32b_invoked"]) + int(
        out["semantic_32b_invoked"]
    )
    return out


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
            request_id=snapshot.request_id,
            case_id=snapshot.case_id,
        )
        out["latency_by_stage_s"]["pipeline"] = round(time.time() - t_pipe, 3)

        out.update(map_integration_result_telemetry(result))
        out["shadow_completed"] = True
        lin = out.get("attempt_lineage")
        if isinstance(lin, dict) and lin.get("request_id") and snapshot.request_id:
            if lin.get("request_id") != snapshot.request_id:
                out["provenance_integrity_failure"] = True
                out["provenance_integrity_reason"] = (
                    "lineage_request_id_mismatch_vs_snapshot"
                )
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
