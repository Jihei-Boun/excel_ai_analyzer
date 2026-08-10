"""Benchmark case schema loader.

Optional fields are intentional — cases differ in what can be verified.
Exact full-plan JSON match is never required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tests.benchmark import CASES_DIR


@dataclass
class ExpectedSpec:
    route: str | None = None  # system | retrieval | analysis_plan | legacy_fallback | pandasai | failure_safe
    required_operations: list[str] = field(default_factory=list)
    forbidden_operations: list[str] = field(default_factory=list)
    expected_columns: dict[str, Any] = field(default_factory=dict)
    forbidden_columns: list[str] = field(default_factory=list)
    expected_result: dict[str, Any] = field(default_factory=dict)
    result_tolerance: float = 1e-6
    allow_semantic_warning: bool = False
    expect_plan_validation_error: bool = False
    expect_retry: bool = False
    expect_safe_failure: bool = False
    interpreter_grounding: bool = False
    notes: str = ""


@dataclass
class BenchmarkCase:
    id: str
    dataset: str
    question: str
    domain: str = "generic"
    profile: str = "generic"
    tags: list[str] = field(default_factory=list)
    expected: ExpectedSpec = field(default_factory=ExpectedSpec)
    # Deterministic CI: feed this plan instead of calling LLM
    fixed_plan: dict[str, Any] | None = None
    # Optional second plan after retry feedback (deterministic retry simulation)
    fixed_plan_retry: dict[str, Any] | None = None
    # Live-only case (skipped in deterministic unless fixed_plan present)
    live_only: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


def _parse_expected(data: dict[str, Any] | None) -> ExpectedSpec:
    data = data or {}
    return ExpectedSpec(
        route=data.get("route"),
        required_operations=list(data.get("required_operations") or []),
        forbidden_operations=list(data.get("forbidden_operations") or []),
        expected_columns=dict(data.get("expected_columns") or {}),
        forbidden_columns=list(data.get("forbidden_columns") or []),
        expected_result=dict(data.get("expected_result") or {}),
        result_tolerance=float(data.get("result_tolerance") or 1e-6),
        allow_semantic_warning=bool(data.get("allow_semantic_warning") or False),
        expect_plan_validation_error=bool(data.get("expect_plan_validation_error") or False),
        expect_retry=bool(data.get("expect_retry") or False),
        expect_safe_failure=bool(data.get("expect_safe_failure") or False),
        interpreter_grounding=bool(data.get("interpreter_grounding") or False),
        notes=str(data.get("notes") or ""),
    )


def load_case_dict(data: dict[str, Any]) -> BenchmarkCase:
    return BenchmarkCase(
        id=str(data["id"]),
        dataset=str(data["dataset"]),
        question=str(data["question"]),
        domain=str(data.get("domain") or data.get("profile") or "generic"),
        profile=str(data.get("profile") or "generic"),
        tags=[str(t) for t in (data.get("tags") or [])],
        expected=_parse_expected(data.get("expected")),
        fixed_plan=data.get("fixed_plan"),
        fixed_plan_retry=data.get("fixed_plan_retry"),
        live_only=bool(data.get("live_only") or False),
        raw=data,
    )


def load_cases_from_file(path: Path) -> list[BenchmarkCase]:
    text = path.read_text(encoding="utf-8")
    payload = yaml.safe_load(text)
    if payload is None:
        return []
    if isinstance(payload, dict) and "cases" in payload:
        items = payload["cases"]
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError(f"Unsupported case file format: {path}")
    return [load_case_dict(item) for item in items]


def iter_case_files(cases_dir: Path | None = None) -> list[Path]:
    root = cases_dir or CASES_DIR
    return sorted(root.glob("*.yaml")) + sorted(root.glob("*.yml"))


def load_all_cases(
    *,
    cases_dir: Path | None = None,
    domains: list[str] | None = None,
    tags: list[str] | None = None,
) -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    for path in iter_case_files(cases_dir):
        cases.extend(load_cases_from_file(path))
    if domains:
        allow = {d.lower() for d in domains}
        cases = [c for c in cases if c.domain.lower() in allow]
    if tags:
        allow_tags = {t.lower() for t in tags}
        cases = [
            c
            for c in cases
            if allow_tags.intersection({t.lower() for t in c.tags})
        ]
    return cases
