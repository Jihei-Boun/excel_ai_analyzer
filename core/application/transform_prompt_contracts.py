"""Natural-language transform request contract (Phase I2-D3B).

Trusted outer request: source path, request_id, output_directory, timeout,
model, and explicit policies. The LLM does not own these fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from core.application.contracts import (
    DEFAULT_TIMEOUT_SECONDS,
    ContractError,
)
from core.application.transform_contracts import (
    FILL_POLICIES,
    TRANSFORM_CONTRACT_VERSION,
    TRANSFORM_OPERATION,
    TransformSource,
    _parse_output_directory,
    _parse_request_id,
    _parse_source,
    _parse_timeout,
    _reject_source_output_overwrite,
    _reject_unknown_keys,
)
from core.constants import DEFAULT_OLLAMA_BASE_URL, DEFAULT_OLLAMA_MODEL

MAX_PROMPT_CHARS = 8_000

GROUP_SCOPE_INCLUDE_DESCENDANTS = "include_descendants_and_subtotals"
OUTPUT_MODE_COPY_NEW_SHEET = "copy_with_new_sheet"

GROUP_SCOPES = frozenset({GROUP_SCOPE_INCLUDE_DESCENDANTS})
OUTPUT_MODES = frozenset({OUTPUT_MODE_COPY_NEW_SHEET})

_TOP_KEYS = frozenset(
    {
        "contract_version",
        "operation",
        "request_id",
        "source",
        "user_prompt",
        "output_directory",
        "timeout_seconds",
        "model",
        "policies",
    }
)
_POLICY_KEYS = frozenset({"group_scope", "output_mode", "unmerge_fill_policy"})
_MODEL_OBJECT_KEYS = frozenset({"base_url", "name"})


@dataclass(frozen=True)
class TransformPromptPolicies:
    group_scope: str
    output_mode: str
    unmerge_fill_policy: str

    def to_dict(self) -> dict[str, str]:
        return {
            "group_scope": self.group_scope,
            "output_mode": self.output_mode,
            "unmerge_fill_policy": self.unmerge_fill_policy,
        }


@dataclass(frozen=True)
class TransformPromptModel:
    base_url: str
    name: str

    def to_dict(self) -> dict[str, str]:
        return {"base_url": self.base_url, "name": self.name}


@dataclass(frozen=True)
class TransformPromptRequest:
    contract_version: str
    request_id: str
    operation: str
    source: TransformSource
    user_prompt: str
    output_directory: str
    timeout_seconds: float
    model: TransformPromptModel
    policies: TransformPromptPolicies

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "operation": self.operation,
            "request_id": self.request_id,
            "source": self.source.to_dict(),
            "user_prompt": self.user_prompt,
            "output_directory": self.output_directory,
            "timeout_seconds": self.timeout_seconds,
            "model": self.model.to_dict(),
            "policies": self.policies.to_dict(),
        }


def parse_transform_prompt_request(
    payload: TransformPromptRequest | dict[str, Any] | None,
) -> TransformPromptRequest:
    if isinstance(payload, TransformPromptRequest):
        return payload
    if not isinstance(payload, dict):
        raise ContractError("invalid_request", "Request must be a JSON object.")
    _reject_unknown_keys(payload, _TOP_KEYS, context="request")

    version = str(payload.get("contract_version") or "").strip()
    if version != TRANSFORM_CONTRACT_VERSION:
        raise ContractError(
            "unsupported_contract_version",
            f"Unsupported contract_version: {version!r}. Expected {TRANSFORM_CONTRACT_VERSION}.",
        )
    operation = str(payload.get("operation") or "").strip()
    if operation != TRANSFORM_OPERATION:
        raise ContractError(
            "invalid_operation",
            f"Unsupported operation: {operation!r}. Expected {TRANSFORM_OPERATION!r}.",
        )
    request_id = _parse_request_id(payload.get("request_id"))
    timeout_seconds = _parse_timeout(payload.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    output_directory = _parse_output_directory(payload.get("output_directory"))
    source = _parse_source(payload.get("source"))
    _reject_source_output_overwrite(source.path, output_directory)
    user_prompt = _parse_user_prompt(payload.get("user_prompt"))
    policies = _parse_policies(payload.get("policies"))
    model = _parse_model(payload.get("model"))
    return TransformPromptRequest(
        contract_version=TRANSFORM_CONTRACT_VERSION,
        request_id=request_id,
        operation=TRANSFORM_OPERATION,
        source=source,
        user_prompt=user_prompt,
        output_directory=output_directory,
        timeout_seconds=timeout_seconds,
        model=model,
        policies=policies,
    )


def assemble_explicit_transform_payload(
    request: TransformPromptRequest,
    transformations: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Trusted outer fields + planner transformations. LLM does not own paths."""
    return {
        "contract_version": TRANSFORM_CONTRACT_VERSION,
        "operation": TRANSFORM_OPERATION,
        "request_id": request.request_id,
        "source": {"path": request.source.path},
        "output_directory": request.output_directory,
        "timeout_seconds": request.timeout_seconds,
        "output_policy": {"source_immutable": True, "create_copy": True},
        "transformations": [dict(item) for item in transformations],
    }


def _parse_user_prompt(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError("missing_user_prompt", "user_prompt is required.")
    if len(raw) > MAX_PROMPT_CHARS:
        raise ContractError(
            "invalid_user_prompt",
            f"user_prompt exceeds {MAX_PROMPT_CHARS} characters.",
        )
    return raw


def _parse_policies(raw: Any) -> TransformPromptPolicies:
    if not isinstance(raw, dict):
        raise ContractError(
            "invalid_policies",
            "policies must be an object with group_scope, output_mode, and unmerge_fill_policy.",
        )
    _reject_unknown_keys(raw, _POLICY_KEYS, context="policies")
    group_scope = str(raw.get("group_scope") or "").strip()
    output_mode = str(raw.get("output_mode") or "").strip()
    fill_policy = str(raw.get("unmerge_fill_policy") or "").strip()
    if group_scope not in GROUP_SCOPES:
        raise ContractError(
            "invalid_policies",
            "policies.group_scope is missing or unsupported.",
        )
    if output_mode not in OUTPUT_MODES:
        raise ContractError(
            "invalid_policies",
            "policies.output_mode is missing or unsupported.",
        )
    if fill_policy not in FILL_POLICIES:
        raise ContractError(
            "invalid_policies",
            "policies.unmerge_fill_policy is missing or unsupported.",
        )
    return TransformPromptPolicies(
        group_scope=group_scope,
        output_mode=output_mode,
        unmerge_fill_policy=fill_policy,
    )


def _parse_model(raw: Any) -> TransformPromptModel:
    if raw is None or raw == "":
        return TransformPromptModel(base_url=DEFAULT_OLLAMA_BASE_URL, name=DEFAULT_OLLAMA_MODEL)
    if isinstance(raw, str) and raw.strip():
        return TransformPromptModel(base_url=DEFAULT_OLLAMA_BASE_URL, name=raw.strip())
    if not isinstance(raw, dict):
        raise ContractError("invalid_model", "model must be a string or {base_url, name} object.")
    _reject_unknown_keys(raw, _MODEL_OBJECT_KEYS, context="model")
    base_url = str(raw.get("base_url") or DEFAULT_OLLAMA_BASE_URL).strip()
    name = str(raw.get("name") or DEFAULT_OLLAMA_MODEL).strip()
    if not base_url or not name:
        raise ContractError("invalid_model", "model.base_url and model.name are required.")
    return TransformPromptModel(base_url=base_url, name=name)
