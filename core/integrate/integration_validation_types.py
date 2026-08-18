"""Phase 16: IntegrationPlan validation result contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from core.integrate.integration_contracts import (
    FAILURE_TYPE_ALIAS,
    FAILURE_TYPE_AMBIGUITY,
    FAILURE_TYPE_RESULT,
    FAILURE_TYPE_STRUCTURAL,
    classify_integration_failure_codes,
    retry_mode_for_failure_type,
)


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
    """Structured feedback for Planner retry (no answer keys prescribed).

    Phase 21: distinguish structural repair vs semantic regenerate vs ambiguity.
    """
    codes = [i.code for i in result.errors]
    failure_type = classify_integration_failure_codes(codes)
    retry_mode = retry_mode_for_failure_type(failure_type)

    lines: list[str] = [
        "Failure stage: integration_plan_validation",
        f"valid: {result.valid}",
        f"failure_type: {failure_type}",
        f"retry_mode: {retry_mode}",
    ]
    if result.errors:
        lines.append("The integration plan cannot be executed because:")
    for issue in result.errors:
        lines.append(_format_issue(issue))
    for issue in result.warnings:
        lines.append(_format_issue(issue, prefix="WARNING"))
    for issue in result.infos[:8]:
        lines.append(_format_issue(issue, prefix="INFO"))

    if failure_type == FAILURE_TYPE_AMBIGUITY:
        lines.append(
            "Previous plan failed because ambiguous or unsupported relationship "
            "evidence remained unresolved. "
            "Do not arbitrarily pick among near-tied singleton keys. "
            "Composite relationship observations in the context are facts only — "
            "do not invent keys. "
            "Use a materially different interpretation if supported by the evidence and "
            "user request, or return status=cannot_plan."
        )
        if "join_against_unrelated" in codes or "union_incompatible_schema" in codes:
            lines.append(
                "Sources appear unrelated or schema-incompatible for the attempted "
                "combine operation. Prefer status=cannot_plan with empty steps rather "
                "than repeating the same unsupported union/join."
            )
        if "ambiguous_key_selection" in codes:
            lines.append(
                "The previous singleton-key interpretation was already rejected. "
                "Composite relationship evidence may exist in the observations. "
                "Re-evaluate the relationship without repeating the same unsupported "
                "singleton interpretation. Do not invent key lists."
            )
    elif failure_type in {FAILURE_TYPE_STRUCTURAL, FAILURE_TYPE_ALIAS}:
        lines.append(
            "This is a structural_contract_failure"
            + (
                " (alias/schema naming)"
                if failure_type == FAILURE_TYPE_ALIAS
                else ""
            )
            + ". "
            "Prefer repairing the previous plan: keep the same integration strategy family "
            "when the composition matches the user request; fix only contract violations "
            "(missing/renamed columns, aliases, step outputs, params shape). "
            "Do not invent keys or swap to an unrelated strategy. "
            "Semantic operation sequence can remain; downstream references must match "
            "declared intermediate schemas."
        )
        if "nonexistent_column" in codes or "missing_column" in codes:
            lines.append(
                "The previous step does not produce a referenced column. "
                "Review the declared output aliases and downstream dependencies. "
                "Do not repeat the same unresolved downstream column reference. "
                "Do not invent substitute column names that are not declared by prior steps."
            )
        if (
            "final_grain_contradiction" in codes
            or "final_required_field_missing" in codes
            or "required_field_permanently_lost" in codes
            or "required_field_not_materializable" in codes
            or "join_key_dropped_in_final_projection" in codes
        ):
            lines.append("Failure stage: integration_plan_validation")
            lines.append("Failure type: final_requirement_preservation")
            # Declared vs observed (evidence only — no prescribed ops/columns)
            if previous_plan and isinstance(previous_plan, dict):
                req = previous_plan.get("final_output_requirements") or {}
                if req:
                    lines.append(
                        "Declared final requirement: "
                        f"grain={req.get('grain')!r}; "
                        f"one_row_represents={req.get('one_row_represents')!r}; "
                        f"required_columns={req.get('required_columns')!r}"
                    )
            for issue in result.errors:
                if issue.code in {
                    "required_field_permanently_lost",
                    "final_required_field_missing",
                    "join_key_dropped_in_final_projection",
                    "final_grain_contradiction",
                }:
                    lines.append(
                        f"Observed plan effect: code={issue.code}; {issue.message}"
                    )
                    break
            lines.append(
                "Invariant: The final plan must remain consistent with its own "
                "declared final-output contract."
            )
            lines.append(
                "A declared required field may have become unavailable, "
                "the declared grain may conflict with a later collapsing "
                "transformation, or the final projection may no longer represent "
                "the declared output semantics. "
                "Do not invent specific replacement operations or column names."
            )
            lines.append(
                "retry_mode_hint: repair when the integration chain is sound and only "
                "the final transformation violates the declared contract; "
                "regenerate when the operation family itself conflicts with the "
                "declared grain/fields."
            )
            for issue in result.errors:
                if issue.code in {
                    "required_field_permanently_lost",
                    "final_required_field_missing",
                    "join_key_dropped_in_final_projection",
                }:
                    lost_op = (issue.details or {}).get("lost_at_op") or (
                        issue.details or {}
                    ).get("select_step")
                    if lost_op:
                        lines.append(
                            "Evidence: a declared final-output field or identifying "
                            f"join key became unavailable around step/op {lost_op!r}."
                        )
                        break
        if "union_incompatible_schema" in codes:
            lines.append(
                "Observed plan effect: union under the chosen column policy is "
                "schema-incompatible. Consider whether a prior rename is required "
                "to align representations before stacking rows. "
                "Do not invent column mappings that are not supported by observations."
            )
        if failure_type == FAILURE_TYPE_ALIAS or "missing_metric_output" in codes:
            lines.append(
                "Aggregate alias contract: explicit alias in the plan is authoritative; "
                "if omitted, the structural default is the source column name. "
                "Downstream steps must use that same declared name."
            )
    elif failure_type == FAILURE_TYPE_RESULT:
        lines.append(
            "Previous plan failed a result/safety invariant. "
            "Do not repeat the same unsafe integration strategy. "
            "Use a materially different strategy if supported by the evidence, "
            "or return status=cannot_plan."
        )
    else:
        lines.append(
            "This is a semantic_failure. Regenerate with a materially different composition "
            "that still matches the user request and evidence. "
            "Do not repeat the previously rejected approach unchanged."
        )

    lines.append(
        "Do not automatically invent or swap keys/operations. "
        "Do not prescribe specific column names that are not in the observations."
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
