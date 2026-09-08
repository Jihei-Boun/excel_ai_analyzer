"""Streamlit-free facade for workbook transformation (Phase I2-D2).

parse_transform_request → deterministic executor → JSON-safe TransformResponse.
Does not dispatch through the CLI, routing layer, or LLM planner.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from core.application.artifacts import json_safe, sha256_file
from core.application.contracts import (
    ContractError,
    ErrorInfo,
    SafetyInfo,
    TimingInfo,
)
from core.application.transform_contracts import (
    TRANSFORM_CONTRACT_VERSION,
    TransformRequest,
    TransformResponse,
    parse_transform_request,
)
from core.application.transform_executor import execute_transform

_REQUEST_STAGE = "request_validation"
_VALIDATION_STAGE = "workbook_validation"
_SUMMARY_LIMIT = 240


def transform_excel(payload: TransformRequest | dict[str, Any] | None) -> dict[str, Any]:
    """Public Python entry: JSON-like transform request → JSON-like response."""
    started = time.perf_counter()
    request_id = _peek_request_id(payload)
    source_path: Path | None = None
    source_sha: str | None = None
    try:
        parsed = parse_transform_request(payload)
        request_id = parsed.request_id
        source_path = Path(parsed.source.path)
        source_sha = sha256_file(source_path)
        result = execute_transform(parsed)
        _assert_source_unchanged(source_path, source_sha)
        response = TransformResponse(
            contract_version=TRANSFORM_CONTRACT_VERSION,
            request_id=parsed.request_id,
            status="success",
            text=_summary(result.applied_operations),
            applied_operations=result.applied_operations,
            artifacts=(result.artifact,),
            warnings=result.warnings,
            safety=SafetyInfo(validation_status="passed", unsafe_output_blocked=False),
            timing=TimingInfo(elapsed_ms=_elapsed_ms(started)),
            source_immutable_verified=True,
            output_contained=bool(result.output_contained),
        )
    except ContractError as exc:
        mutated = _source_mutation_error(source_path, source_sha)
        response = _error_response(
            request_id=request_id,
            exc=mutated or exc,
            elapsed_ms=_elapsed_ms(started),
            source_verified=source_sha is not None and mutated is None,
        )
    except Exception:
        mutated = _source_mutation_error(source_path, source_sha)
        response = _error_response(
            request_id=request_id,
            exc=mutated
            or ContractError(
                "execution_failed",
                "Workbook transformation failed.",
                stage="execution",
                retryable=False,
            ),
            elapsed_ms=_elapsed_ms(started),
            source_verified=source_sha is not None and mutated is None,
        )
    return json_safe(response.to_dict())


def _error_response(
    *,
    request_id: str,
    exc: ContractError,
    elapsed_ms: int,
    source_verified: bool,
) -> TransformResponse:
    status = _status_for(exc)
    return TransformResponse(
        contract_version=TRANSFORM_CONTRACT_VERSION,
        request_id=str(request_id or ""),
        status=status,
        text=exc.message,
        applied_operations=(),
        artifacts=(),
        warnings=(),
        safety=SafetyInfo(validation_status="not_applicable", unsafe_output_blocked=False),
        timing=TimingInfo(elapsed_ms=elapsed_ms),
        error=ErrorInfo(
            code=exc.code,
            message=exc.message,
            stage=exc.stage,
            retryable=bool(exc.retryable),
        ),
        source_immutable_verified=source_verified,
        output_contained=False,
    )


def _status_for(exc: ContractError) -> str:
    if exc.stage == _REQUEST_STAGE:
        return "invalid_request"
    if exc.stage == _VALIDATION_STAGE:
        return "validation_failed"
    return "execution_failed"


def _summary(applied: tuple[Any, ...]) -> str:
    names = [getattr(item, "op", "") for item in applied]
    text = f"Applied {len(applied)} transformation(s): {', '.join(names)}."
    if len(text) > _SUMMARY_LIMIT:
        return text[: _SUMMARY_LIMIT - 1] + "…"
    return text


def _assert_source_unchanged(path: Path | None, expected_sha: str | None) -> None:
    mutated = _source_mutation_error(path, expected_sha)
    if mutated is not None:
        raise mutated


def _source_mutation_error(path: Path | None, expected_sha: str | None) -> ContractError | None:
    if path is None or expected_sha is None:
        return None
    try:
        actual = sha256_file(path)
    except OSError:
        return None
    if actual != expected_sha:
        return ContractError(
            "source_mutated",
            "Source workbook changed during transformation.",
            stage="execution",
            retryable=False,
        )
    return None


def _peek_request_id(payload: TransformRequest | dict[str, Any] | None) -> str:
    if isinstance(payload, TransformRequest):
        return payload.request_id
    if isinstance(payload, dict):
        return str(payload.get("request_id") or "")
    return ""


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
