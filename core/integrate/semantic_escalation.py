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
) -> IntegrationPipelineResult:
    """Experimental path: failure escalation (optional) + semantic escalation (optional).

    Not wired to route_multi. Does not change production defaults.
    """
    cfg = config or SemanticEscalationConfig()
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
    verification = run_semantic_verification(
        user_prompt=user_prompt,
        plan=base.plan.to_dict(),
        result=None,
        understanding=None,
        variant=SEMANTIC_VERIFIER_VARIANT,
        model=cfg.verifier_model,
        base_url=base_url,
        chat_json_fn=verifier_chat_json_fn,
    )
    verifier_elapsed_s = round(time.time() - t_ver0, 3)
    trace.verifier = verification.to_dict()
    base.metadata["semantic_verifier_invoked"] = True
    base.metadata["semantic_verifier"] = verification.to_dict()
    base.metadata["semantic_verifier_elapsed_s"] = verifier_elapsed_s

    escalate, reason = _should_semantic_escalate(
        verification, uncertain_policy=cfg.uncertain_policy
    )
    if not escalate:
        trace.semantic_escalation_reason = reason
        base.metadata["semantic_escalation"] = trace.to_dict()
        return base

    if int(cfg.max_semantic_escalations) < 1:
        trace.notes.append("semantic_escalation_budget_zero")
        base.metadata["semantic_escalation"] = trace.to_dict()
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
    return merged
