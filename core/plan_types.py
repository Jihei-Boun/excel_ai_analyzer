"""구조화 실행 계획·스키마 추론 타입."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


SUPPORTED_OPERATIONS = frozenset(
    {
        "aggregate_merge",
        "select",
        "rename",
        "filter",
        "classify_rows",
        "groupby",
        "aggregate",
        "join",
        "concat",
        "sort",
        "derive_column",
        "insert_subtotal",
        "insert_grand_total",
        "export_workbook",
    }
)

SUPPORTED_AGG_FUNCS = frozenset({"sum", "first", "last", "max", "min", "mean", "count"})


def _coerce_int(value: Any) -> int | None:
    """LLM JSON에서 정수로 해석 가능한 값만 받는다. dict 등은 None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            try:
                num = float(text)
            except ValueError:
                return None
            return int(num) if num.is_integer() else None
    if isinstance(value, dict):
        for key in ("row", "index", "value", "header_row", "n"):
            if key in value:
                return _coerce_int(value[key])
        for nested in value.values():
            parsed = _coerce_int(nested)
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, (list, tuple)) and len(value) == 1:
        return _coerce_int(value[0])
    return None


def _parse_header_rows(raw: Any) -> list[int]:
    """header_rows를 list[int]로 정규화. LLM이 dict/혼합 형식을 줘도 깨지지 않게."""
    if raw is None:
        return [0]
    if isinstance(raw, dict):
        items: list[Any] = []
        for key in ("start", "end", "from", "to", "row", "index", "value", "header_row", "n"):
            if key in raw:
                items.append(raw[key])
        if not items:
            items = list(raw.values())
    elif isinstance(raw, (int, float, str)):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return [0]

    rows: list[int] = []
    for item in items:
        parsed = _coerce_int(item)
        if parsed is not None and parsed not in rows:
            rows.append(parsed)
    return rows or [0]


@dataclass
class FileSchema:
    """단일 소스에 대한 LLM 스키마 추론 결과."""

    source: str
    header_rows: list[int] = field(default_factory=lambda: [0])
    identifier_columns: list[str] = field(default_factory=list)
    label_columns: list[str] = field(default_factory=list)
    additive_columns: list[str] = field(default_factory=list)
    non_additive_columns: list[str] = field(default_factory=list)
    summary_row_labels: list[str] = field(default_factory=list)
    column_renames: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: str) -> "FileSchema":
        renames = data.get("column_renames") or {}
        if not isinstance(renames, dict):
            renames = {}
        return cls(
            source=source,
            header_rows=_parse_header_rows(data.get("header_rows")),
            identifier_columns=[str(x) for x in (data.get("identifier_columns") or [])],
            label_columns=[str(x) for x in (data.get("label_columns") or [])],
            additive_columns=[str(x) for x in (data.get("additive_columns") or [])],
            non_additive_columns=[str(x) for x in (data.get("non_additive_columns") or [])],
            summary_row_labels=[str(x) for x in (data.get("summary_row_labels") or data.get("summary_rows") or [])],
            column_renames={str(k): str(v) for k, v in renames.items()},
            notes=[str(x) for x in (data.get("notes") or [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "header_rows": list(self.header_rows),
            "identifier_columns": list(self.identifier_columns),
            "label_columns": list(self.label_columns),
            "additive_columns": list(self.additive_columns),
            "non_additive_columns": list(self.non_additive_columns),
            "summary_row_labels": list(self.summary_row_labels),
            "column_renames": dict(self.column_renames),
            "notes": list(self.notes),
        }


@dataclass
class DerivedRowSpec:
    type: str
    label: str | None = None
    group_by: str | None = None
    composition: str | None = None  # codes | remainder | all
    codes: list[str] = field(default_factory=list)
    code_column: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DerivedRowSpec":
        return cls(
            type=str(data.get("type") or "subtotal"),
            label=(str(data["label"]) if data.get("label") is not None else None),
            group_by=(str(data["group_by"]) if data.get("group_by") is not None else None),
            composition=(
                str(data["composition"]) if data.get("composition") is not None else None
            ),
            codes=[str(x) for x in (data.get("codes") or [])],
            code_column=(
                str(data["code_column"]) if data.get("code_column") is not None else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self.type}
        if self.label is not None:
            payload["label"] = self.label
        if self.group_by is not None:
            payload["group_by"] = self.group_by
        if self.composition is not None:
            payload["composition"] = self.composition
        if self.codes:
            payload["codes"] = list(self.codes)
        if self.code_column is not None:
            payload["code_column"] = self.code_column
        return payload


@dataclass
class ExecutionPlan:
    """허용된 범용 연산만 담는 실행 계획."""

    operation: str
    sources: list[str]
    group_keys: list[str] = field(default_factory=list)
    aggregations: dict[str, str] = field(default_factory=dict)
    renames: dict[str, str] = field(default_factory=dict)
    excluded_row_types: list[str] = field(default_factory=list)
    summary_row_labels: list[str] = field(default_factory=list)
    derived_rows: list[DerivedRowSpec] = field(default_factory=list)
    sort_by: list[str] = field(default_factory=list)
    blank_repeated_group_labels: bool = True
    group_display_column: str | None = None
    include_normalized_source_sheets: bool = True
    integrated_sheet_name: str = "통합"
    sheet_name_map: dict[str, str] = field(default_factory=dict)
    column_order: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionPlan":
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        derived = [
            DerivedRowSpec.from_dict(item)
            for item in (data.get("derived_rows") or [])
            if isinstance(item, dict)
        ]
        aggregations = data.get("aggregations") or {}
        if not isinstance(aggregations, dict):
            aggregations = {}
        renames = data.get("renames") or {}
        if not isinstance(renames, dict):
            renames = {}
        sheet_map = output.get("sheet_name_map") or data.get("sheet_name_map") or {}
        if not isinstance(sheet_map, dict):
            sheet_map = {}

        operation = str(data.get("operation") or "aggregate_merge")
        return cls(
            operation=operation,
            sources=[str(x) for x in (data.get("sources") or [])],
            group_keys=[str(x) for x in (data.get("group_keys") or [])],
            aggregations={str(k): str(v) for k, v in aggregations.items()},
            renames={str(k): str(v) for k, v in renames.items()},
            excluded_row_types=[str(x) for x in (data.get("excluded_row_types") or [])],
            summary_row_labels=[
                str(x) for x in (data.get("summary_row_labels") or data.get("summary_rows") or [])
            ],
            derived_rows=derived,
            sort_by=[str(x) for x in (data.get("sort_by") or [])],
            blank_repeated_group_labels=bool(
                data.get("blank_repeated_group_labels", True)
            ),
            group_display_column=(
                str(data["group_display_column"])
                if data.get("group_display_column") is not None
                else None
            ),
            include_normalized_source_sheets=bool(
                output.get(
                    "include_normalized_source_sheets",
                    data.get("include_normalized_source_sheets", True),
                )
            ),
            integrated_sheet_name=str(
                output.get("integrated_sheet_name")
                or data.get("integrated_sheet_name")
                or "통합"
            ),
            sheet_name_map={str(k): str(v) for k, v in sheet_map.items()},
            column_order=[str(x) for x in (data.get("column_order") or [])],
            raw=dict(data),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "sources": list(self.sources),
            "group_keys": list(self.group_keys),
            "aggregations": dict(self.aggregations),
            "renames": dict(self.renames),
            "excluded_row_types": list(self.excluded_row_types),
            "summary_row_labels": list(self.summary_row_labels),
            "derived_rows": [item.to_dict() for item in self.derived_rows],
            "sort_by": list(self.sort_by),
            "blank_repeated_group_labels": self.blank_repeated_group_labels,
            "group_display_column": self.group_display_column,
            "column_order": list(self.column_order),
            "output": {
                "include_normalized_source_sheets": self.include_normalized_source_sheets,
                "integrated_sheet_name": self.integrated_sheet_name,
                "sheet_name_map": dict(self.sheet_name_map),
            },
        }


@dataclass
class ValidationIssue:
    level: str  # error | warning
    code: str
    message: str


@dataclass
class ValidationReport:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [item for item in self.issues if item.level == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [item for item in self.issues if item.level == "warning"]

    def summary_text(self) -> str:
        if self.ok and not self.issues:
            return "검증 통과"
        parts = [f"[{item.level}] {item.message}" for item in self.issues]
        return " · ".join(parts)


@dataclass
class IntegrateResult:
    integrated: pd.DataFrame
    sheets: dict[str, pd.DataFrame]
    plan: ExecutionPlan
    schemas: dict[str, FileSchema]
    validation: ValidationReport
    workbook_path: str | None = None
    workbook_bytes: bytes | None = None
    reply: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
