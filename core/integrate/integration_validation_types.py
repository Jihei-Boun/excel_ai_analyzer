"""Phase 16: IntegrationPlan validation result contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


@dataclass
class IntegrationValidationIssue:
    code: str
    severity: str  # error | warning | info
    message: str
    step_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntegrationValidationResult:
    """Outcome of validate_integration_plan — does not mutate the plan."""

    valid: bool
    errors: list[IntegrationValidationIssue] = field(default_factory=list)
    warnings: list[IntegrationValidationIssue] = field(default_factory=list)
    infos: list[IntegrationValidationIssue] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    lineage: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "infos": [i.to_dict() for i in self.infos],
            "metadata": dict(self.metadata),
            "lineage": list(self.lineage),
        }

    @property
    def issues(self) -> list[IntegrationValidationIssue]:
        return [*self.errors, *self.warnings, *self.infos]


def format_integration_validation_feedback(
    result: IntegrationValidationResult,
    *,
    previous_plan: dict[str, Any] | None = None,
) -> list[str]:
    """Structured feedback for Phase 18 Planner retry (no answer keys prescribed)."""
    lines: list[str] = [
        "Failure stage: integration_plan_validation",
        f"valid: {result.valid}",
    ]
    if result.errors:
        lines.append("The integration plan cannot be executed because:")
    for issue in result.errors:
        lines.append(_format_issue(issue))
    for issue in result.warnings:
        lines.append(_format_issue(issue, prefix="WARNING"))
    for issue in result.infos[:8]:
        lines.append(_format_issue(issue, prefix="INFO"))

    codes = {i.code for i in result.errors}
    if "ambiguous_key_selection" in codes:
        lines.append(
            "Previous plan failed because ambiguous relationship evidence remained unresolved. "
            "Do not arbitrarily pick among near-tied singleton keys. "
            "Use a materially different integration strategy if supported by the evidence, "
            "or return status=cannot_plan."
        )
    elif codes & {
        "insufficient_evidence_forced_join",
        "join_against_unrelated",
        "many_to_many_join_risk",
    }:
        lines.append(
            "Previous plan failed because the join was unsafe given relationship evidence. "
            "Use a materially different integration strategy if supported by the evidence, "
            "or return status=cannot_plan."
        )
    lines.append(
        "Do not automatically invent or swap keys/operations. "
        "Regenerate the integration plan using relationship evidence and the user request."
    )
    if previous_plan is not None:
        import json

        try:
            compact = json.dumps(previous_plan, ensure_ascii=False, default=str)
            if len(compact) > 1200:
                compact = compact[:1200] + "…"
            lines.append(f"Previous invalid plan: {compact}")
        except Exception:  # noqa: BLE001
            lines.append("Previous invalid plan: (unserializable)")
    return lines


def _format_issue(issue: IntegrationValidationIssue, *, prefix: str | None = None) -> str:
    head = prefix or "ERROR"
    step = f"Step: {issue.step_id}" if issue.step_id else "Step: (plan)"
    parts = [
        f"{head}",
        step,
        f"Code: {issue.code}",
        f"Message: {issue.message}",
    ]
    if issue.details:
        evid = []
        for k, v in issue.details.items():
            if k in {"suggested_op", "use_key", "recommended_key"}:
                continue  # never surface answer-prescribing fields
            evid.append(f"{k}: {v}")
        if evid:
            parts.append("Evidence:")
            parts.extend(f"  {x}" for x in evid[:12])
    return "\n".join(parts)
