"""Multi-file benchmark case schema (optional fields allowed)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tests.benchmark_multi import CASES_DIR


@dataclass
class ResultCompareSpec:
    sort_by: list[str] = field(default_factory=list)
    key_column: str | None = None
    value_column: str | None = None
    expected_result: dict[str, Any] = field(default_factory=dict)
    expected_row_count: int | None = None
    required_columns: list[str] = field(default_factory=list)
    # Phase 23 — benchmark semantic expectations (evaluation only; never passed to Planner)
    expected_metrics: list[dict[str, Any]] = field(default_factory=list)
    expected_grain: str | None = None  # detail | group | summary | None
    rtol: float = 1e-6
    atol: float = 1e-6


@dataclass
class ExpectedSpec:
    # success | cannot_plan | failed — or list of allowed
    pipeline_status: list[str] = field(default_factory=lambda: ["success"])
    safety_outcome: str = "safe"  # safe | unsafe
    relationship_allowed: list[str] = field(default_factory=list)
    relationship_forbidden: list[str] = field(default_factory=list)
    required_operations: list[str] = field(default_factory=list)
    forbidden_operations: list[str] = field(default_factory=list)
    required_input_files: list[str] = field(default_factory=list)
    join_left_keys: list[str] = field(default_factory=list)
    join_right_keys: list[str] = field(default_factory=list)
    join_how: list[str] = field(default_factory=list)
    result: ResultCompareSpec = field(default_factory=ResultCompareSpec)
    allow_plan_validation_block: bool = False
    notes: str = ""


@dataclass
class MultiBenchmarkCase:
    id: str
    files: list[str]
    prompt: str
    scenario: str = "generic"
    domain: str = "generic"
    tags: list[str] = field(default_factory=list)
    expected: ExpectedSpec = field(default_factory=ExpectedSpec)
    fixed_relationships: list[dict[str, Any]] = field(default_factory=list)
    fixed_plan: dict[str, Any] | None = None
    fixed_plan_retry: dict[str, Any] | None = None
    live_only: bool = False
    # Phase 20: fixed-plan retry-diversity cases are meaningless under live LLM.
    deterministic_only: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


def _as_status_list(value: Any) -> list[str]:
    if value is None:
        return ["success"]
    if isinstance(value, list):
        return [str(x) for x in value]
    return [str(value)]


def _parse_result(data: dict[str, Any] | None) -> ResultCompareSpec:
    data = data or {}
    compare = dict(data.get("result_compare") or {})
    metrics_raw = data.get("expected_metrics") or compare.get("expected_metrics") or []
    return ResultCompareSpec(
        sort_by=[str(x) for x in (compare.get("sort_by") or data.get("sort_by") or [])],
        key_column=(compare.get("key_column") or data.get("key_column")),
        value_column=(compare.get("value_column") or data.get("value_column")),
        expected_result=dict(
            compare.get("expected_result") or data.get("expected_result") or {}
        ),
        expected_row_count=(
            data.get("expected_row_count")
            if data.get("expected_row_count") is not None
            else compare.get("expected_row_count")
        ),
        required_columns=[
            str(x) for x in (data.get("required_columns") or compare.get("required_columns") or [])
        ],
        expected_metrics=[dict(m) for m in metrics_raw if isinstance(m, dict)],
        expected_grain=(
            str(data.get("expected_grain") or compare.get("expected_grain") or "") or None
        ),
        rtol=float(compare.get("rtol") or data.get("rtol") or 1e-6),
        atol=float(compare.get("atol") or data.get("atol") or 1e-6),
    )


def _parse_expected(data: dict[str, Any] | None) -> ExpectedSpec:
    data = data or {}
    rel = dict(data.get("relationship") or {})
    join = dict(data.get("join") or {})
    result_block = dict(data.get("result") or {})
    # merge top-level expected_result convenience
    if data.get("expected_result") and not result_block.get("expected_result"):
        result_block["expected_result"] = data.get("expected_result")
    if data.get("required_columns") and not result_block.get("required_columns"):
        result_block["required_columns"] = data.get("required_columns")
    if data.get("expected_row_count") is not None and result_block.get("expected_row_count") is None:
        result_block["expected_row_count"] = data.get("expected_row_count")
    if data.get("result_compare"):
        result_block["result_compare"] = data.get("result_compare")

    return ExpectedSpec(
        pipeline_status=_as_status_list(data.get("pipeline_status")),
        safety_outcome=str(data.get("safety_outcome") or "safe"),
        relationship_allowed=[str(x) for x in (rel.get("allowed") or [])],
        relationship_forbidden=[str(x) for x in (rel.get("forbidden") or [])],
        required_operations=[str(x) for x in (data.get("required_operations") or [])],
        forbidden_operations=[str(x) for x in (data.get("forbidden_operations") or [])],
        required_input_files=[str(x) for x in (data.get("required_input_files") or [])],
        join_left_keys=[str(x) for x in (join.get("left_keys") or [])],
        join_right_keys=[str(x) for x in (join.get("right_keys") or [])],
        join_how=[str(x) for x in (join.get("how") or [])] if join.get("how") else [],
        result=_parse_result(result_block),
        allow_plan_validation_block=bool(data.get("allow_plan_validation_block") or False),
        notes=str(data.get("notes") or ""),
    )


def load_case_dict(data: dict[str, Any]) -> MultiBenchmarkCase:
    files = data.get("files") or []
    return MultiBenchmarkCase(
        id=str(data["id"]),
        files=[str(f) for f in files],
        prompt=str(data.get("prompt") or data.get("question") or ""),
        scenario=str(data.get("scenario") or "generic"),
        domain=str(data.get("domain") or "generic"),
        tags=[str(t) for t in (data.get("tags") or [])],
        expected=_parse_expected(data.get("expected")),
        fixed_relationships=list(data.get("fixed_relationships") or []),
        fixed_plan=data.get("fixed_plan"),
        fixed_plan_retry=data.get("fixed_plan_retry"),
        live_only=bool(data.get("live_only") or False),
        deterministic_only=bool(data.get("deterministic_only") or False),
        raw=data,
    )


def load_cases_from_file(path: Path) -> list[MultiBenchmarkCase]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload is None:
        return []
    if isinstance(payload, dict) and "cases" in payload:
        items = payload["cases"]
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError(f"Unsupported case file: {path}")
    return [load_case_dict(item) for item in items]


def load_all_cases(cases_dir: Path | None = None) -> list[MultiBenchmarkCase]:
    root = cases_dir or CASES_DIR
    cases: list[MultiBenchmarkCase] = []
    for path in sorted(root.glob("*.yaml")):
        cases.extend(load_cases_from_file(path))
    return cases
