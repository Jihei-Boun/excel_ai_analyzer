"""Phase 28: Evidence-based planner model escalation (selection only).

Does NOT rewrite plans, does NOT use scenario labels / domain / expected answers,
and does NOT bypass validators. Escalation uses only runtime pipeline evidence
after the fast planner path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Codes that indicate final-projection / field-survival friction where a stronger
# planner often recovers (Phase 27: lookup / rename_join). Intentionally narrow —
# do NOT treat union_incompatible_schema or unsafe join gates as escalation triggers.
_ESCALATION_TRIGGER_CODES = frozenset(
    {
        "join_key_dropped_in_final_projection",
        "required_field_not_materializable",
        # Phase 30: same final-contract family as projection failures — generic,
        # not scenario-routed. Enables recoverable grain-consistency exhaustion
        # to escalate under existing evidence-based policy.
        "final_grain_contradiction",
    }
)

_UNSAFE_ONLY_CODES = frozenset(
    {
        "ambiguous_key_selection",
        "insufficient_evidence_forced_join",
        "join_against_unrelated",
        "many_to_many_join_risk",
        "extreme_row_amplification",
    }
)

# Expected-negative structural refuses — escalate would be unnecessary cost.
_NON_ESCALATE_DOMINANT_CODES = frozenset(
    {
        "union_incompatible_schema",
        "many_to_many_join_risk",
        "ambiguous_key_selection",
        "join_against_unrelated",
        "insufficient_evidence_forced_join",
    }
)


@dataclass(frozen=True)
class PlannerModelStrategy:
    """Separates model selection from integration semantics."""

    fast_model: str = "qwen2.5:7b"
    strong_model: str = "qwen3:32b"
    enable_escalation: bool = False
    strong_max_retries: int = 2


@dataclass
class EscalationDecision:
    should_escalate: bool
    reason_code: str | None = None
    evidence: list[str] = field(default_factory=list)
    from_model: str | None = None
    to_model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_escalate": self.should_escalate,
            "reason_code": self.reason_code,
            "evidence": list(self.evidence),
            "from_model": self.from_model,
            "to_model": self.to_model,
        }


def default_strategy(*, enable_escalation: bool = False) -> PlannerModelStrategy:
    return PlannerModelStrategy(enable_escalation=enable_escalation)


def _flatten_retry_codes(retry_log: list[dict[str, Any]]) -> list[str]:
    codes: list[str] = []
    for entry in retry_log:
        if not isinstance(entry, dict):
            continue
        for c in entry.get("failure_codes") or []:
            codes.append(str(c))
    return codes


def _is_trigger_code(code: str) -> bool:
    return code in _ESCALATION_TRIGGER_CODES


def should_escalate_after_fast_path(
    *,
    status: str,
    retry_log: list[dict[str, Any]],
    metadata: dict[str, Any],
    strategy: PlannerModelStrategy,
) -> EscalationDecision:
    """Decide whether to invoke the strong planner.

    Production-safe evidence only — never golden / case_id / domain / prompt keywords.
    Conservative: prefer no escalation when ambiguous.
    """
    fast = strategy.fast_model
    strong = strategy.strong_model

    if not strategy.enable_escalation:
        return EscalationDecision(False, from_model=fast)

    # Safety: legitimate cannot_plan must not be forced through a stronger model.
    if status == "cannot_plan":
        return EscalationDecision(
            False,
            reason_code="skip_cannot_plan",
            evidence=["fast_path_status=cannot_plan"],
            from_model=fast,
        )

    if status == "success":
        return EscalationDecision(
            False,
            reason_code="skip_success",
            evidence=["fast_path_status=success"],
            from_model=fast,
        )

    if status != "failed":
        return EscalationDecision(
            False,
            reason_code="skip_unknown_status",
            evidence=[f"fast_path_status={status}"],
            from_model=fast,
        )

    codes = _flatten_retry_codes(retry_log)
    code_set = set(codes)
    meta = metadata or {}
    exhausted = bool(meta.get("exhausted"))
    plan_val_n = int(meta.get("plan_validation_failure_count") or 0)
    exec_n = int(meta.get("execution_failure_count") or 0)
    result_val_n = int(meta.get("result_validation_failure_count") or 0)
    dup_n = int(meta.get("duplicate_plan_count") or 0)
    family_n = int(meta.get("same_family_repeat_count") or 0)
    repeated_final = bool(meta.get("repeated_final_contract_failure"))
    unsafe_blocked = bool(meta.get("validator_blocked_unsafe_plan"))

    evidence: list[str] = [
        "fast_path_status=failed",
        f"exhausted={exhausted}",
        f"plan_validation_failure_count={plan_val_n}",
        f"execution_failure_count={exec_n}",
        f"result_validation_failure_count={result_val_n}",
        f"duplicate_plan_count={dup_n}",
        f"same_family_repeat_count={family_n}",
        f"repeated_final_contract_failure={repeated_final}",
        f"validator_blocked_unsafe_plan={unsafe_blocked}",
    ]
    if codes:
        evidence.append("failure_codes=" + ",".join(sorted(code_set)[:12]))

    trigger_hits = sorted(code_set & _ESCALATION_TRIGGER_CODES)
    only_unsafe = bool(codes) and all(c in _UNSAFE_ONLY_CODES for c in codes)

    if only_unsafe:
        return EscalationDecision(
            False,
            reason_code="skip_unsafe_only_failures",
            evidence=evidence + ["all_failure_codes_are_unsafe_gates"],
            from_model=fast,
        )

    # If dominant failures are expected-negative schema/safety refuses and no
    # projection-trigger codes, do not escalate (avoids FP on incompatible_union /
    # many_to_many exhausted paths that are already safe outcomes).
    dominant_negative = bool(code_set & _NON_ESCALATE_DOMINANT_CODES)
    if dominant_negative and not trigger_hits and result_val_n == 0:
        return EscalationDecision(
            False,
            reason_code="skip_expected_negative_structural",
            evidence=evidence + ["dominant_non_escalate_codes"],
            from_model=fast,
        )

    # Primary trigger: final-projection contract codes seen during exhausted retries.
    if trigger_hits:
        reason = "recoverable_plan_validation_failure"
        if "join_key_dropped_in_final_projection" in trigger_hits:
            reason = "repeated_final_contract_failure" if repeated_final or dup_n or family_n else (
                "recoverable_plan_validation_failure"
            )
        return EscalationDecision(
            True,
            reason_code=reason,
            evidence=evidence + [f"trigger_codes={','.join(trigger_hits)}"],
            from_model=fast,
            to_model=strong,
        )

    # Secondary: result-validation exhaustion (runtime evidence, not golden).
    if result_val_n > 0 and exhausted:
        return EscalationDecision(
            True,
            reason_code="recoverable_result_validation_failure",
            evidence=evidence,
            from_model=fast,
            to_model=strong,
        )

    # Secondary: execution retryable exhaustion without unsafe-only codes.
    if exec_n > 0 and exhausted and not unsafe_blocked:
        return EscalationDecision(
            True,
            reason_code="retry_exhausted_recoverable",
            evidence=evidence,
            from_model=fast,
            to_model=strong,
        )

    return EscalationDecision(
        False,
        reason_code="skip_no_recoverable_evidence",
        evidence=evidence,
        from_model=fast,
    )


def build_escalation_feedback(
    *,
    decision: EscalationDecision,
    retry_log: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> list[str]:
    """Generic evidence summary for the strong planner — never dictates a plan."""
    lines = [
        "Previous planning attempts with a smaller planner exhausted without a valid executable plan.",
        "You must independently produce a correct IntegrationPlan (or cannot_plan).",
        "Do not copy a previous invalid plan. Do not invent keys or relationships.",
    ]
    if decision.reason_code:
        lines.append(f"Escalation reason (observability): {decision.reason_code}")
    stages: list[str] = []
    for e in retry_log:
        if isinstance(e, dict) and e.get("failure_stage"):
            stages.append(str(e["failure_stage"]))
    if stages:
        lines.append("Observed failure stages: " + ", ".join(dict.fromkeys(stages)))
    codes = sorted(set(_flatten_retry_codes(retry_log)))
    if codes:
        lines.append("Observed failure codes: " + ", ".join(codes[:16]))
    if metadata.get("repeated_final_contract_failure"):
        lines.append(
            "Final-output contract validation failed repeatedly "
            "(projection / required fields / grain consistency)."
        )
    if metadata.get("duplicate_plan_count"):
        lines.append("Identical plans were repeated after rejection.")
    if metadata.get("same_family_repeat_count"):
        lines.append("The same operation-family pattern was repeated after rejection.")
    lines.append(
        "If relationship evidence is insufficient or ambiguous, return cannot_plan — "
        "do not force an unsafe join or union."
    )
    return lines
