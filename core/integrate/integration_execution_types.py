"""Phase 17: IntegrationPlan execution result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class IntegrationExecutionError(Exception):
    """Structured runtime / gate failure (no semantic repair hints)."""

    code: str
    message: str
    step_id: str | None = None
    op: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # noqa: D105
        return f"{self.code}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "step_id": self.step_id,
            "op": self.op,
            "details": dict(self.details),
        }


@dataclass
class IntegrationStepExecutionResult:
    step_id: str
    op: str
    inputs: list[str]
    output: str
    status: str  # success | failed | skipped
    input_shapes: dict[str, tuple[int, int]] = field(default_factory=dict)
    output_shape: tuple[int, int] | None = None
    columns_before: dict[str, list[str]] = field(default_factory=dict)
    columns_after: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    lineage: dict[str, Any] = field(default_factory=dict)
    error: IntegrationExecutionError | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "op": self.op,
            "inputs": list(self.inputs),
            "output": self.output,
            "status": self.status,
            "input_shapes": {k: list(v) for k, v in self.input_shapes.items()},
            "output_shape": list(self.output_shape) if self.output_shape else None,
            "columns_before": {k: list(v) for k, v in self.columns_before.items()},
            "columns_after": list(self.columns_after),
            "metadata": dict(self.metadata),
            "lineage": dict(self.lineage),
            "error": self.error.to_dict() if self.error else None,
        }


@dataclass
class IntegrationExecutionResult:
    """Outcome of execute_integration_plan — never mutates plan or sources."""

    success: bool
    final_output: pd.DataFrame | None = None
    final_output_name: str | None = None
    datasets: dict[str, pd.DataFrame] = field(default_factory=dict)
    step_results: list[IntegrationStepExecutionResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    lineage: list[dict[str, Any]] = field(default_factory=list)
    error: IntegrationExecutionError | None = None

    def to_dict(self, *, include_dataframes: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "success": self.success,
            "final_output_name": self.final_output_name,
            "step_results": [s.to_dict() for s in self.step_results],
            "metadata": dict(self.metadata),
            "lineage": list(self.lineage),
            "error": self.error.to_dict() if self.error else None,
            "dataset_names": sorted(self.datasets.keys()),
        }
        if self.final_output is not None:
            payload["final_shape"] = [int(self.final_output.shape[0]), int(self.final_output.shape[1])]
            payload["final_columns"] = [str(c) for c in self.final_output.columns]
        if include_dataframes:
            payload["datasets"] = {
                k: v.to_dict(orient="list") for k, v in self.datasets.items()
            }
        return payload
