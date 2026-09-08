"""Natural-language transform facade (Phase I2-D3B).

parse prompt request → observe → plan → parse_transform_request → transform_excel.
Does not change explicit transform_excel() behavior.

Limitation: timeout_seconds is passed to chat_json as the HTTP timeout. This
in-process facade cannot forcibly abort the planner plus executor once they
are running.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from core.application.artifacts import json_safe, sha256_file
from core.application.contracts import ContractError, ErrorInfo, SafetyInfo, TimingInfo, WarningItem
from core.application.transform_contracts import (
    TRANSFORM_CONTRACT_VERSION,
    TransformResponse,
)
from core.application.transform_headless import transform_excel
from core.application.transform_observer import WorkbookObservation, observe_workbook
from core.application.transform_planner import PlannerResult, plan_transformations
from core.application.transform_prompt_contracts import (
    TransformPromptRequest,
    assemble_explicit_transform_payload,
    parse_transform_prompt_request,
)

_REQUEST_STAGE = "request_validation"
_VALIDATION_STAGE = "workbook_validation"
_PLANNING = "planning"
_SUMMARY_LIMIT = 240


def transform_excel_from_prompt(
    payload: TransformPromptRequest | dict[str, Any] | None,
    *,
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
    observe_fn: Callable[[str], WorkbookObservation] | None = None,
    plan_fn: Callable[..., PlannerResult] | None = None,
) -> dict[str, Any]:
    """Public Python entry: natural-language transform request → JSON-safe response."""
    started = time.perf_counter()
    request_id = _peek_request_id(payload)
    source_path: Path | None = None
    source_sha: str | None = None
    extra_warnings: list[WarningItem] = []
    try:
        parsed = parse_transform_prompt_request(payload)
        request_id = parsed.request_id
        source_path = Path(parsed.source.path)
        source_sha = sha256_file(source_path)
        observer = observe_fn or observe_workbook
        observation = observer(parsed.source.path)
        extra_warnings.extend(observation.warnings)
        planner = plan_fn or plan_transformations
        plan_kwargs: dict[str, Any] = {}
        if chat_json_fn is not None and plan_fn is None:
            plan_kwargs["chat_json_fn"] = chat_json_fn
        plan = planner(parsed, observation, **plan_kwargs)
        extra_warnings.extend(plan.warnings)
        if plan.status != "planned":
            _assert_unchanged(source_path, source_sha)
            return json_safe(
                _cannot_plan_response(
                    request_id=parsed.request_id,
                    plan=plan,
                    warnings=tuple(extra_warnings),
                    elapsed_ms=_elapsed_ms(started),
                    source_verified=True,
                ).to_dict()
            )
        explicit = assemble_explicit_transform_payload(parsed, plan.transformations)
        response = transform_excel(explicit)
        _assert_unchanged(source_path, source_sha)
        merged = _merge_warnings(response, extra_warnings)
        if isinstance(merged.get("text"), str) and plan.status == "planned":
            merged["text"] = _bound_text(str(merged.get("text") or "Transformation planned and applied."))
        return json_safe(merged)
    except ContractError as exc:
        _assert_unchanged(source_path, source_sha)
        return json_safe(
            _error_response(
                request_id=request_id,
                exc=exc,
                elapsed_ms=_elapsed_ms(started),
                source_verified=source_sha is not None,
                warnings=tuple(extra_warnings),
            ).to_dict()
        )
    except Exception:
        _assert_unchanged(source_path, source_sha)
        return json_safe(
            _error_response(
                request_id=request_id,
                exc=ContractError(
                    "execution_failed",
                    "Workbook transformation failed.",
                    stage="execution",
                    retryable=False,
                ),
                elapsed_ms=_elapsed_ms(started),
                source_verified=source_sha is not None,
                warnings=tuple(extra_warnings),
            ).to_dict()
        )


def _cannot_plan_response(
    *,
    request_id: str,
    plan: PlannerResult,
    warnings: tuple[WarningItem, ...],
    elapsed_ms: int,
    source_verified: bool,
) -> TransformResponse:
    message = plan.message or "The request cannot be planned safely."
    return TransformResponse(
        contract_version=TRANSFORM_CONTRACT_VERSION,
        request_id=request_id,
        status="cannot_plan",
        text=_bound_text(message),
        applied_operations=(),
        artifacts=(),
        warnings=warnings,
        safety=SafetyInfo(validation_status="not_applicable", unsafe_output_blocked=False),
        timing=TimingInfo(elapsed_ms=elapsed_ms),
        error=ErrorInfo(
            code=plan.reason_code or "cannot_plan",
            message=_bound_text(message),
            stage=_PLANNING,
            retryable=False,
        ),
        source_immutable_verified=source_verified,
        output_contained=False,
    )


def _error_response(
    *,
    request_id: str,
    exc: ContractError,
    elapsed_ms: int,
    source_verified: bool,
    warnings: tuple[WarningItem, ...],
) -> TransformResponse:
    return TransformResponse(
        contract_version=TRANSFORM_CONTRACT_VERSION,
        request_id=str(request_id or ""),
        status=_status_for(exc),
        text=_bound_text(exc.message),
        applied_operations=(),
        artifacts=(),
        warnings=warnings,
        safety=SafetyInfo(validation_status="not_applicable", unsafe_output_blocked=False),
        timing=TimingInfo(elapsed_ms=elapsed_ms),
        error=ErrorInfo(
            code=exc.code,
            message=_bound_text(exc.message),
            stage=exc.stage,
            retryable=bool(exc.retryable),
        ),
        source_immutable_verified=source_verified,
        output_contained=False,
    )


def _status_for(exc: ContractError) -> str:
    if exc.code == "model_unavailable":
        return "model_unavailable"
    if exc.code in {"timeout", "model_timeout"}:
        return "timeout"
    if exc.code == "cannot_plan" or exc.stage == _PLANNING:
        return "cannot_plan"
    if exc.stage == _REQUEST_STAGE:
        return "invalid_request"
    if exc.stage == _VALIDATION_STAGE:
        return "validation_failed"
    return "execution_failed"


def _merge_warnings(response: dict[str, Any], extra: list[WarningItem]) -> dict[str, Any]:
    existing = list(response.get("warnings") or [])
    seen = {(item.get("code"), item.get("message")) for item in existing if isinstance(item, dict)}
    for item in extra:
        payload = item.to_dict()
        key = (payload.get("code"), payload.get("message"))
        if key in seen:
            continue
        existing.append(payload)
        seen.add(key)
    response["warnings"] = existing[:40]
    return response


def _assert_unchanged(path: Path | None, expected: str | None) -> None:
    if path is None or expected is None:
        return
    try:
        actual = sha256_file(path)
    except OSError:
        return
    if actual != expected:
        raise ContractError(
            "source_mutated",
            "Source workbook changed during transformation.",
            stage="execution",
            retryable=False,
        )


def _peek_request_id(payload: TransformPromptRequest | dict[str, Any] | None) -> str:
    if isinstance(payload, TransformPromptRequest):
        return payload.request_id
    if isinstance(payload, dict):
        return str(payload.get("request_id") or "")
    return ""


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _bound_text(message: str) -> str:
    text = str(message or "").replace("\x00", "").strip()
    if len(text) > _SUMMARY_LIMIT:
        return text[: _SUMMARY_LIMIT - 1] + "…"
    return text
