"""Phase 35: experimental semantic-verification-triggered escalation (NOT production).

After deterministic candidate success, optionally invoke frozen 7B V1 verifier.
On FAIL (and optionally UNCERTAIN), request exactly one strong replan.
Strong replan uses the same Plan Validator / Executor / Result Validator.

Does NOT modify route_multi, failure-based Phase 28 escalation policy,
verifier prompt, or prescribe semantic repairs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from core.integrate.integration_pipeline import (
    DEFAULT_MAX_RETRIES,
    IntegrationPipelineResult,
    _attach_model_strategy_meta,
    _merge_escalation_result,
    _run_integration_attempt_loop,
    run_integration_pipeline,
)
from core.integrate.integration_plan_types import IntegrationPlan
from core.integrate.planner_model_strategy import (
    EscalationDecision,
    PlannerModelStrategy,
)
from core.integrate.relationship_types import CrossFileUnderstanding
from core.integrate.semantic_verifier import (
    SemanticVerificationResult,
    run_semantic_verification,
)

# Frozen Phase 33/34 verifier configuration
SEMANTIC_VERIFIER_MODEL = "qwen2.5:7b"
SEMANTIC_VERIFIER_VARIANT = "V1"
MAX_SEMANTIC_ESCALATIONS = 1


@dataclass
class SemanticEscalationConfig:
    """Experimental knobs — not production defaults."""

    enable_failure_escalation: bool = True
    enable_semantic_escalation: bool = True
    uncertain_policy: str = "escalate"  # escalate | accept
    max_semantic_escalations: int = MAX_SEMANTIC_ESCALATIONS
    verifier_model: str = SEMANTIC_VERIFIER_MODEL
    strong_model: str = "qwen3:32b"
    strong_max_retries: int = 2
    reverify_strong: bool = False  # Phase 35 primary: False


@dataclass
class SemanticEscalationTrace:
    verifier: dict[str, Any] | None = None
    semantic_escalated: bool = False
    semantic_escalation_reason: str | None = None
    failure_escalated: bool = False
    original_status: str | None = None
    original_plan: dict[str, Any] | None = None
    strong_status: str | None = None
    strong_plan: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verifier": self.verifier,
            "semantic_escalated": self.semantic_escalated,
            "semantic_escalation_reason": self.semantic_escalation_reason,
            "failure_escalated": self.failure_escalated,
            "original_status": self.original_status,
            "original_plan": self.original_plan,
            "strong_status": self.strong_status,
            "strong_plan": self.strong_plan,
            "notes": list(self.notes),
        }


def build_semantic_replan_feedback(
    *,
    previous_plan: IntegrationPlan | dict[str, Any] | None,
    verification: SemanticVerificationResult,
) -> list[str]:
    """Generic semantic failure evidence — never dictates ops/keys/columns."""
    lines = [
        "Previous IntegrationPlan passed structural / deterministic validation "
        "but was judged semantically inconsistent with the user's request.",
        "Produce a new IntegrationPlan (or cannot_plan) that better satisfies "
        "the user's request using CrossFileUnderstanding evidence.",
        "Do not invent sources, columns, or keys. Do not copy the previous plan "
        "if it does not answer the request.",
        "Do not force unsafe joins/unions when evidence is insufficient.",
    ]
    if verification.verdict:
        lines.append(f"Semantic verifier verdict: {verification.verdict}")
    if verification.reason_code:
        lines.append(f"Semantic verifier reason_code: {verification.reason_code}")
    for ev in (verification.evidence or [])[:6]:
        lines.append(f"Semantic evidence: {ev}")
    # Attach previous plan as observability only (planner may ignore)
    plan_d = (
        previous_plan.to_dict()
        if isinstance(previous_plan, IntegrationPlan)
        else (previous_plan if isinstance(previous_plan, dict) else None)
    )
    if plan_d:
        ops = [s.get("op") for s in (plan_d.get("steps") or []) if isinstance(s, dict)]
        grain = ((plan_d.get("final_output_requirements") or {}) or {}).get("grain")
        lines.append(
            f"Previous plan ops/grain (observability only): ops={ops}, grain={grain!r}"
        )
    return lines


def _should_semantic_escalate(
    verification: SemanticVerificationResult,
    *,
    uncertain_policy: str,
) -> tuple[bool, str | None]:
    if verification.verdict == "fail":
        return True, "semantic_verifier_fail"
    if verification.verdict == "uncertain":
        if uncertain_policy == "escalate":
            return True, "semantic_verifier_uncertain"
        return False, "semantic_uncertain_accepted"
    if verification.verdict == "parse_failed":
        # Conservative: do not escalate on verifier parse failure
        return False, "semantic_verifier_parse_failed"
    return False, "semantic_verifier_pass"


def run_integration_pipeline_semantic_experimental(
    user_prompt: str,
    sources: dict[str, pd.DataFrame],
    understanding: CrossFileUnderstanding | dict[str, Any],
    *,
    config: SemanticEscalationConfig | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_url: str = "http://localhost:11434",
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
    build_plan_fn: Callable[..., IntegrationPlan] | None = None,
    verifier_chat_json_fn: Callable[..., dict[str, Any]] | None = None,
    request_id: str | None = None,
    case_id: str | None = None,
) -> IntegrationPipelineResult:
    """Experimental path: failure escalation (optional) + semantic escalation (optional).

    Not wired to route_multi. Does not change production defaults.

    request_id / case_id are frozen at entry. Live env is not re-read after
    this point for lineage or capture identity.
    """
    cfg = config or SemanticEscalationConfig()
    # Freeze identity at entry (caller/snapshot). Do not re-read env later.
    try:
        from core.integrate.verifier_invocation_capture import (
            env_case_id,
            env_request_id,
        )

        frozen_request_id = request_id if request_id is not None else env_request_id()
        frozen_case_id = case_id if case_id is not None else env_case_id()
    except Exception:
        frozen_request_id = request_id
        frozen_case_id = case_id

    strategy = PlannerModelStrategy(
        fast_model="qwen2.5:7b",
        strong_model=cfg.strong_model,
        enable_escalation=cfg.enable_failure_escalation,
        strong_max_retries=cfg.strong_max_retries,
    )

    base = run_integration_pipeline(
        user_prompt,
        sources,
        understanding,
        max_retries=max_retries,
        base_url=base_url,
        model="qwen2.5:7b",
        chat_json_fn=chat_json_fn,
        build_plan_fn=build_plan_fn,
        model_strategy=strategy if cfg.enable_failure_escalation else None,
    )

    trace = SemanticEscalationTrace(
        failure_escalated=bool((base.metadata or {}).get("escalated")),
        original_status=base.status,
        original_plan=base.plan.to_dict() if base.plan else None,
    )
    base.metadata = dict(base.metadata or {})
    base.metadata["semantic_escalation"] = trace.to_dict()
    base.metadata["failure_escalation_32b"] = bool(trace.failure_escalated)
    base.metadata["semantic_escalation_32b"] = False
    base.metadata["semantic_verifier_invoked"] = False

    if not cfg.enable_semantic_escalation:
        return base

    # Only final deterministic successes are verified (blanket on successes)
    if base.status != "success" or base.plan is None:
        trace.notes.append("skip_semantic_verify_non_success")
        base.metadata["semantic_escalation"] = trace.to_dict()
        return base

    t_ver0 = time.time()
    # Phase 39D-V1: ground verifier with source schemas from live frames.
    # Must be active in Shadow path; without this, final_schema mode is a no-op.
    und_dict = (
        understanding.to_dict()
        if hasattr(understanding, "to_dict")
        else understanding
        if isinstance(understanding, dict)
        else None
    )
    source_schemas = {
        str(name): [str(c) for c in df.columns]
        for name, df in sources.items()
    }

    # Phase 39O: attempt lineage (observation only; never alters escalate decision).
    lineage = None
    verified_attempt = None
    try:
        from core.integrate.attempt_lineage import (
            DISPOSITION_REJECTED_BY_SEMANTIC_VERIFIER,
            DISPOSITION_SUPERSEDED_BY_FAILURE_ESCALATION,
            RequestAttemptLineage,
            STAGE_FAILURE_ESCALATION_SUCCESS,
            STAGE_FAST_PATH,
            STAGE_FAST_SUCCESS,
            STAGE_SEMANTIC_STRONG,
            TRIGGER_FAILURE_ESCALATION,
            TRIGGER_NONE,
            TRIGGER_SEMANTIC_ESCALATION,
            plan_fingerprint,
            safe_lineage_call,
        )
        from core.integrate.verifier_invocation_capture import (
            get_record_for_attempt,
            update_last_lineage_finalization,
        )

        lineage = RequestAttemptLineage(
            request_id=frozen_request_id, case_id=frozen_case_id
        )
        parent_id = None
        if bool(trace.failure_escalated):
            parent = lineage.create_attempt(
                stage=STAGE_FAST_PATH,
                plan=None,
                planner_model="qwen2.5:7b",
                planner_path="fast",
                escalation_trigger=TRIGGER_NONE,
                notes=["failure_escalation_parent"],
            )
            fp = (base.metadata or {}).get("fast_path_plan_fingerprint")
            if isinstance(fp, str) and fp:
                parent.plan_fingerprint = fp
            lineage.set_disposition(
                parent.attempt_id, DISPOSITION_SUPERSEDED_BY_FAILURE_ESCALATION
            )
            parent_id = parent.attempt_id
            verified_attempt = lineage.create_attempt(
                stage=STAGE_FAILURE_ESCALATION_SUCCESS,
                plan=base.plan,
                planner_model=str(
                    (base.metadata or {}).get("final_model") or cfg.strong_model
                ),
                planner_path="strong",
                parent_attempt_id=parent_id,
                escalation_trigger=TRIGGER_FAILURE_ESCALATION,
            )
        else:
            verified_attempt = lineage.create_attempt(
                stage=STAGE_FAST_SUCCESS,
                plan=base.plan,
                planner_model=str(
                    (base.metadata or {}).get("final_model") or "qwen2.5:7b"
                ),
                planner_path="fast",
                escalation_trigger=TRIGGER_NONE,
            )
    except Exception as _lin_exc:  # noqa: BLE001
        lineage = None
        verified_attempt = None
        try:
            base.metadata["attempt_lineage_error"] = (
                f"{type(_lin_exc).__name__}: {_lin_exc}"
            )
        except Exception:
            pass

    lin_ctx = None
    if lineage is not None and verified_attempt is not None:
        try:
            lin_ctx = lineage.capture_fields_for(verified_attempt.attempt_id)
        except Exception:
            lin_ctx = None

    verification = run_semantic_verification(
        user_prompt=user_prompt,
        plan=base.plan.to_dict(),
        result=None,
        understanding=und_dict,
        variant=SEMANTIC_VERIFIER_VARIANT,
        model=cfg.verifier_model,
        base_url=base_url,
        chat_json_fn=verifier_chat_json_fn,
        source_schemas=source_schemas,
        materialization_mode="final_schema_expr_partition",
        lineage_context=lin_ctx,
    )
    # Attach verifier_invocation_id to attempt (best-effort).
    if lineage is not None and verified_attempt is not None:
        try:
            inv_id = getattr(verification, "verifier_invocation_id", None)
            if not inv_id:
                rec = get_record_for_attempt(verified_attempt.attempt_id)
                if (
                    isinstance(rec, dict)
                    and rec.get("verifier_invocation_id")
                    and rec.get("request_id")
                    in {None, lineage.request_id, verified_attempt.request_id}
                ):
                    inv_id = rec.get("verifier_invocation_id")
            if inv_id:
                lineage.attach_verifier_invocation(
                    verified_attempt.attempt_id,
                    str(inv_id),
                )
        except Exception:
            pass
    verifier_elapsed_s = round(time.time() - t_ver0, 3)
    trace.verifier = verification.to_dict()
    base.metadata["semantic_verifier_invoked"] = True
    base.metadata["semantic_verifier"] = verification.to_dict()
    base.metadata["semantic_verifier_elapsed_s"] = verifier_elapsed_s

    escalate, reason = _should_semantic_escalate(
        verification, uncertain_policy=cfg.uncertain_policy
    )
    # Phase 39L: observational escalation attach (does not alter decision).
    try:
        from core.integrate.verifier_invocation_capture import (
            capture_enabled,
            update_last_escalation,
        )

        if capture_enabled():
            update_last_escalation(
                escalation_triggered=bool(escalate),
                escalation_type=str(reason) if reason is not None else None,
                request_id=frozen_request_id,
                attempt_id=(
                    verified_attempt.attempt_id if verified_attempt is not None else None
                ),
            )
    except Exception:
        pass

    if not escalate:
        trace.semantic_escalation_reason = reason
        base.metadata["semantic_escalation"] = trace.to_dict()
        # Phase 39O: verified attempt is final when no semantic escalation.
        if lineage is not None and verified_attempt is not None:
            try:
                lineage.set_final(verified_attempt.attempt_id)
                base.metadata["attempt_lineage"] = lineage.to_dict()
                base.metadata["verified_attempt_id"] = verified_attempt.attempt_id
                base.metadata["final_attempt_id"] = lineage.final_attempt_id
                base.metadata["verified_plan_fingerprint"] = (
                    verified_attempt.plan_fingerprint
                )
                from core.integrate.verifier_invocation_capture import (
                    update_last_lineage_finalization as _ulf,
                )
                _ulf(
                    became_final=True,
                    final_attempt_id=lineage.final_attempt_id,
                    attempt_disposition="final",
                    request_id=frozen_request_id,
                    attempt_id=verified_attempt.attempt_id,
                )
            except Exception as _fin_exc:  # noqa: BLE001
                base.metadata["attempt_lineage_error"] = (
                    f"{type(_fin_exc).__name__}: {_fin_exc}"
                )
        return base

    if int(cfg.max_semantic_escalations) < 1:
        trace.notes.append("semantic_escalation_budget_zero")
        base.metadata["semantic_escalation"] = trace.to_dict()
        if lineage is not None and verified_attempt is not None:
            try:
                lineage.set_final(verified_attempt.attempt_id)
                base.metadata["attempt_lineage"] = lineage.to_dict()
                base.metadata["verified_attempt_id"] = verified_attempt.attempt_id
                base.metadata["final_attempt_id"] = lineage.final_attempt_id
                base.metadata["verified_plan_fingerprint"] = (
                    verified_attempt.plan_fingerprint
                )
                from core.integrate.verifier_invocation_capture import (
                    update_last_lineage_finalization as _ulf,
                )
                _ulf(
                    became_final=True,
                    final_attempt_id=lineage.final_attempt_id,
                    attempt_disposition="final",
                    request_id=frozen_request_id,
                    attempt_id=verified_attempt.attempt_id,
                )
            except Exception as _fin_exc:  # noqa: BLE001
                base.metadata["attempt_lineage_error"] = (
                    f"{type(_fin_exc).__name__}: {_fin_exc}"
                )
        return base

    # Exactly one strong semantic replan
    feedback = build_semantic_replan_feedback(
        previous_plan=base.plan, verification=verification
    )
    decision = EscalationDecision(
        True,
        reason_code=reason,
        evidence=[
            "deterministic_success",
            f"semantic_verdict={verification.verdict}",
            f"semantic_reason={verification.reason_code}",
        ],
        from_model="qwen2.5:7b",
        to_model=cfg.strong_model,
    )
    t_strong0 = time.time()
    strong = _run_integration_attempt_loop(
        user_prompt,
        sources,
        understanding,
        max_retries=cfg.strong_max_retries,
        base_url=base_url,
        model=cfg.strong_model,
        chat_json_fn=chat_json_fn,
        build_plan_fn=build_plan_fn,
        initial_feedback=feedback,
        path_label="semantic_strong",
    )
    strong_elapsed_s = round(time.time() - t_strong0, 3)
    trace.semantic_escalated = True
    trace.semantic_escalation_reason = reason
    trace.strong_status = strong.status
    trace.strong_plan = strong.plan.to_dict() if strong.plan else None

    merged = _merge_escalation_result(base, strong, decision=decision, strategy=strategy)
    # Preserve both escalation attributions
    merged.metadata = dict(merged.metadata or {})
    merged.metadata["failure_escalation_32b"] = bool(trace.failure_escalated)
    merged.metadata["semantic_escalation_32b"] = True
    merged.metadata["semantic_verifier_invoked"] = True
    merged.metadata["semantic_verifier"] = verification.to_dict()
    merged.metadata["semantic_verifier_elapsed_s"] = verifier_elapsed_s
    merged.metadata["semantic_strong_elapsed_s"] = strong_elapsed_s
    merged.metadata["semantic_escalation"] = trace.to_dict()
    merged.metadata["escalation_source"] = "semantic"
    # If failure escalation already happened, note both (rare on success path)
    if trace.failure_escalated:
        merged.metadata["escalation_source"] = "failure_then_semantic_unreachable"
    merged.metadata["final_path"] = (
        "semantic_escalation_success"
        if strong.status == "success"
        else (
            "semantic_escalation_cannot_plan"
            if strong.status == "cannot_plan"
            else "semantic_escalation_failed"
        )
    )
    # Phase 35 primary: do not re-verify strong result
    if cfg.reverify_strong and strong.status == "success" and strong.plan is not None:
        trace.notes.append("reverify_strong_skipped_in_primary_config")

    # Phase 39O: parent rejected by verifier; child strong attempt is final.
    if lineage is not None and verified_attempt is not None:
        try:
            from core.integrate.attempt_lineage import (
                DISPOSITION_CANNOT_PLAN,
                DISPOSITION_EXECUTION_FAILED,
                DISPOSITION_REJECTED_BY_SEMANTIC_VERIFIER,
                DISPOSITION_SUPERSEDED_BY_SEMANTIC_ESCALATION,
                STAGE_SEMANTIC_STRONG,
                TRIGGER_SEMANTIC_ESCALATION,
            )
            from core.integrate.verifier_invocation_capture import (
                update_last_lineage_finalization as _ulf,
            )

            lineage.set_disposition(
                verified_attempt.attempt_id,
                DISPOSITION_REJECTED_BY_SEMANTIC_VERIFIER,
            )
            # Also mark superseded once child exists.
            child = lineage.create_attempt(
                stage=STAGE_SEMANTIC_STRONG,
                plan=strong.plan,
                planner_model=str(cfg.strong_model),
                planner_path="semantic_strong",
                parent_attempt_id=verified_attempt.attempt_id,
                escalation_trigger=TRIGGER_SEMANTIC_ESCALATION,
            )
            lineage.set_disposition(
                verified_attempt.attempt_id,
                DISPOSITION_SUPERSEDED_BY_SEMANTIC_ESCALATION,
            )
            if strong.status == "success":
                lineage.set_final(child.attempt_id)
            elif strong.status == "cannot_plan":
                lineage.set_disposition(child.attempt_id, DISPOSITION_CANNOT_PLAN)
            else:
                lineage.set_disposition(child.attempt_id, DISPOSITION_EXECUTION_FAILED)
            merged.metadata["attempt_lineage"] = lineage.to_dict()
            merged.metadata["verified_attempt_id"] = verified_attempt.attempt_id
            merged.metadata["final_attempt_id"] = lineage.final_attempt_id
            merged.metadata["verified_plan_fingerprint"] = (
                verified_attempt.plan_fingerprint
            )
            merged.metadata["final_plan_fingerprint"] = child.plan_fingerprint
            _ulf(
                became_final=False,
                final_attempt_id=lineage.final_attempt_id,
                attempt_disposition=DISPOSITION_SUPERSEDED_BY_SEMANTIC_ESCALATION,
                request_id=frozen_request_id,
                attempt_id=verified_attempt.attempt_id,
            )
        except Exception as _fin_exc:  # noqa: BLE001
            merged.metadata["attempt_lineage_error"] = (
                f"{type(_fin_exc).__name__}: {_fin_exc}"
            )
    return merged
