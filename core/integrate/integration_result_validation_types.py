"""Phase 18: Integration result validation contracts + feedback."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

FAILURE_STAGE_PLAN_GENERATION = "integration_plan_generation"
FAILURE_STAGE_PLAN_VALIDATION = "integration_plan_validation"
FAILURE_STAGE_EXECUTION = "integration_execution"
FAILURE_STAGE_RESULT_VALIDATION = "integration_result_validation"


@dataclass
class IntegrationResultValidationIssue:
    code: str
    severity: str
    message: str
    step_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "step_id": self.step_id,
            "details": dict(self.details),
        }


@dataclass
class IntegrationResultValidationResult:
    """Outcome of validate_integration_result — read-only; does not mutate plan/data."""

    valid: bool
    errors: list[IntegrationResultValidationIssue] = field(default_factory=list)
    warnings: list[IntegrationResultValidationIssue] = field(default_factory=list)
    infos: list[IntegrationResultValidationIssue] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    failure_stage: str = FAILURE_STAGE_RESULT_VALIDATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "infos": [i.to_dict() for i in self.infos],
            "metadata": dict(self.metadata),
            "failure_stage": self.failure_stage,
        }

    @property
    def issues(self) -> list[IntegrationResultValidationIssue]:
        return [*self.errors, *self.warnings, *self.infos]


def format_integration_result_validation_feedback(
    result: IntegrationResultValidationResult,
    *,
    previous_plan: dict[str, Any] | None = None,
) -> list[str]:
    """Structured feedback for Planner retry (evidence only, no prescribed fix)."""
    stage = result.failure_stage or FAILURE_STAGE_RESULT_VALIDATION
    lines: list[str] = [
        f"Failure stage: {stage}",
        f"valid: {result.valid}",
    ]
    if result.errors:
        lines.append(
            "The integration plan executed, but the resulting output failed result validation:"
        )
    for issue in result.errors:
        lines.append(_format_issue(issue))
    for issue in result.warnings[:6]:
        lines.append(_format_issue(issue, prefix="WARNING"))
    for issue in result.infos[:4]:
        lines.append(_format_issue(issue, prefix="INFO"))
    codes = [e.code for e in result.errors]
    if "final_required_column_missing" in codes:
        lines.append(
            "The executed plan produced a final output that does not satisfy "
            "the final output requirements declared by the plan. "
            "Review whether later transformations preserve the requested grain and fields."
        )
    lines.append(
        "Do not repeat the previous integration plan unchanged. "
        "Use the available cross-file relationship evidence and execution evidence "
        "to produce a materially safer plan, or return cannot_plan if ambiguity remains."
    )
    if previous_plan is not None:
        import json

        try:
            compact = json.dumps(previous_plan, ensure_ascii=False, default=str)
            if len(compact) > 1200:
                compact = compact[:1200] + "…"
            lines.append(f"Previous rejected plan: {compact}")
        except Exception:  # noqa: BLE001
            lines.append("Previous rejected plan: (unserializable)")
    return lines


def format_integration_execution_feedback(
    execution: Any,
    *,
    previous_plan: dict[str, Any] | None = None,
) -> list[str]:
    """Feedback when Executor returns success=False (no semantic repair)."""
    err = getattr(execution, "error", None)
    lines = [
        f"Failure stage: {FAILURE_STAGE_EXECUTION}",
        "The integration executor could not complete the plan.",
    ]
    if err is not None:
        lines.append(
            "\n".join(
                [
                    "ERROR",
                    f"Step: {getattr(err, 'step_id', None) or '(execution)'}",
                    f"Code: {getattr(err, 'code', 'execution_failed')}",
                    f"Message: {getattr(err, 'message', str(err))}",
                ]
            )
        )
        details = getattr(err, "details", None) or {}
        if details:
            lines.append("Evidence:")
            for k, v in list(details.items())[:12]:
                if k in {"suggested_op", "use_key", "recommended_key"}:
                    continue
                lines.append(f"  {k}: {v}")
    lines.append(
        "Do not invent keys/ops. If the failure is plan-dependent, regenerate a "
        "materially different plan; otherwise return cannot_plan."
    )
    if previous_plan is not None:
        import json

        try:
            compact = json.dumps(previous_plan, ensure_ascii=False, default=str)
            if len(compact) > 1200:
                compact = compact[:1200] + "…"
            lines.append(f"Previous rejected plan: {compact}")
        except Exception:  # noqa: BLE001
            lines.append("Previous rejected plan: (unserializable)")
    return lines


def _format_issue(
    issue: IntegrationResultValidationIssue, *, prefix: str | None = None
) -> str:
    head = prefix or "ERROR"
    step = f"Step: {issue.step_id}" if issue.step_id else "Step: (result)"
    parts = [
        f"{head}",
        step,
        f"Code: {issue.code}",
        f"Message: {issue.message}",
    ]
    if issue.details:
        evid = []
        for k, v in issue.details.items():
            if k in {"suggested_op", "use_key", "recommended_key", "change_how"}:
                continue
            evid.append(f"{k}: {v}")
        if evid:
            parts.append("Evidence:")
            parts.extend(f"  {x}" for x in evid[:12])
    return "\n".join(parts)
