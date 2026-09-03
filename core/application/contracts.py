"""Versioned headless request/response contracts.

This layer does not decide analysis meaning. It validates caller input,
normalizes JSON-safe envelopes, and preserves production router outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CONTRACT_VERSION = "1.0"
SUPPORTED_OPERATIONS = frozenset({"analyze"})
SUPPORTED_ANALYSIS_MODES = frozenset({"single", "multi"})
SUPPORTED_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".csv"})
SUPPORTED_STATUSES = frozenset(
    {
        "success",
        "invalid_request",
        "cannot_plan",
        "validation_failed",
        "model_unavailable",
        "timeout",
        "cancelled",
        "execution_failed",
    }
)

DEFAULT_TIMEOUT_SECONDS = 180.0
MAX_TIMEOUT_SECONDS = 86_400.0
PREVIEW_ROW_LIMIT = 10

SAFE_OUTCOME_META_KEYS = frozenset(
    {
        "filter_summary",
        "filter_note",
        "list_values",
        "list_label",
        "list_groups",
        "workbook_sheets",
    }
)

CLI_EXIT_CODES = {
    "success": 0,
    "execution_failed": 1,
    "invalid_request": 2,
    "cannot_plan": 3,
    "validation_failed": 4,
    "model_unavailable": 5,
    "timeout": 6,
    "cancelled": 7,
}


class ContractError(ValueError):
    """Request/contract validation failure. Not a semantic planning error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str = "request_validation",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.retryable = retryable

    def to_error_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "stage": self.stage,
            "retryable": bool(self.retryable),
        }


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    name: str

    def to_dict(self) -> dict[str, str]:
        return {"base_url": self.base_url, "name": self.name}


@dataclass(frozen=True)
class InputSource:
    source_id: str
    path: str
    sheet: str | int = 0
    display_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "path": self.path,
            "sheet": self.sheet,
            "display_name": self.display_name,
        }


@dataclass(frozen=True)
class AnalyzeRequest:
    contract_version: str
    request_id: str
    operation: str
    inputs: tuple[InputSource, ...]
    user_prompt: str
    analysis_mode: str
    profile_name: str
    model: ModelConfig
    timeout_seconds: float
    output_directory: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "request_id": self.request_id,
            "operation": self.operation,
            "inputs": [item.to_dict() for item in self.inputs],
            "user_prompt": self.user_prompt,
            "analysis_mode": self.analysis_mode,
            "profile_name": self.profile_name,
            "model": self.model.to_dict(),
            "timeout_seconds": self.timeout_seconds,
            "output_directory": self.output_directory,
        }


@dataclass
class Artifact:
    artifact_id: str
    kind: str
    path: str
    media_type: str
    filename: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "path": self.path,
            "media_type": self.media_type,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass
class WarningItem:
    code: str
    message: str
    stage: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "stage": self.stage}


@dataclass
class SafetyInfo:
    validation_status: str
    unsafe_output_blocked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_status": self.validation_status,
            "unsafe_output_blocked": bool(self.unsafe_output_blocked),
        }


@dataclass
class ErrorInfo:
    code: str
    message: str
    stage: str
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "stage": self.stage,
            "retryable": bool(self.retryable),
        }


@dataclass
class TimingInfo:
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, int]:
        return {"elapsed_ms": int(self.elapsed_ms)}


@dataclass
class AnalyzeResponse:
    contract_version: str = CONTRACT_VERSION
    request_id: str = ""
    status: str = "execution_failed"
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    warnings: list[WarningItem] = field(default_factory=list)
    safety: SafetyInfo = field(
        default_factory=lambda: SafetyInfo(validation_status="not_applicable")
    )
    timing: TimingInfo = field(default_factory=TimingInfo)
    error: ErrorInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contract_version": self.contract_version,
            "request_id": self.request_id,
            "status": self.status,
            "text": self.text,
            "data": dict(self.data or empty_result_data()),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "warnings": [item.to_dict() for item in self.warnings],
            "safety": self.safety.to_dict(),
            "timing": self.timing.to_dict(),
        }
        if self.error is not None:
            payload["error"] = self.error.to_dict()
        return payload


def empty_result_data(*, route: str = "") -> dict[str, Any]:
    return {
        "shape": [0, 0],
        "columns": [],
        "preview_records": [],
        "route": route,
    }


def error_response(
    *,
    request_id: str,
    status: str,
    code: str,
    message: str,
    stage: str,
    retryable: bool,
    elapsed_ms: int = 0,
    text: str = "",
    warnings: list[WarningItem] | None = None,
    safety: SafetyInfo | None = None,
    data: dict[str, Any] | None = None,
    artifacts: list[Artifact] | None = None,
) -> AnalyzeResponse:
    if status not in SUPPORTED_STATUSES:
        status = "execution_failed"
    return AnalyzeResponse(
        contract_version=CONTRACT_VERSION,
        request_id=str(request_id or ""),
        status=status,
        text=text or message,
        data=data or empty_result_data(),
        artifacts=list(artifacts or ()),
        warnings=list(warnings or ()),
        safety=safety
        or SafetyInfo(validation_status="not_applicable", unsafe_output_blocked=False),
        timing=TimingInfo(elapsed_ms=elapsed_ms),
        error=ErrorInfo(
            code=code,
            message=message,
            stage=stage,
            retryable=retryable,
        ),
    )
