"""LLM 실행 계획 생성 — 도메인 전용 함수 호출 없음."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from core.llm_client import chat_json
from core.integrate.plan_types import ExecutionPlan, FileSchema, SUPPORTED_OPERATIONS
from core.integrate.schema_infer import build_frame_inventory, semantic_hints_text


def build_execution_plan(
    prompt: str,
    *,
    named_frames: list[tuple[str, pd.DataFrame]],
    schemas: dict[str, FileSchema],
    base_url: str,
    model: str,
    profile_name: str | None = None,
    example_frames: list[tuple[str, pd.DataFrame]] | None = None,
    previous_errors: list[str] | None = None,
    chat_json_fn=chat_json,
) -> ExecutionPlan:
    """사용자 요청 + 스키마 → 구조화 실행 계획 JSON."""
    inventories = {
        name: build_frame_inventory(name, frame)
        for name, frame in named_frames
    }
    schema_payload = {name: schema.to_dict() for name, schema in schemas.items()}

    system = (
        "You are a planning module for a generic Excel integrator. "
        "Given similar tables from multiple files and inferred schemas, "
        "produce ONE JSON execution plan for a deterministic Python engine. "
        "Preferred operation for combining similar numeric tables by key is "
        "'aggregate_merge'. "
        "Do not write pandas code. Do not invent columns. "
        "Use column names AFTER applying each schema's column_renames "
        "(and include a top-level renames map consolidating them). "
        "Exclude summary/subtotal/grand-total rows from aggregation inputs, "
        "then recreate needed derived rows via derived_rows. "
        "For summary compositions use: "
        "subtotal (group_by), summary with composition codes|remainder|all, "
        "or grand_total. "
        "If an example integrated table is provided, match its sheet layout "
        "and derived-row pattern, but keep names taken from the current schemas."
    )

    user_parts = [
        f"User request:\n{prompt}",
        f"Source files:\n{json.dumps(list(inventories.keys()), ensure_ascii=False)}",
        f"Inferred schemas:\n{json.dumps(schema_payload, ensure_ascii=False, indent=2)}",
        f"Inventories:\n{json.dumps(inventories, ensure_ascii=False, indent=2)}",
    ]
    hint = semantic_hints_text(profile_name=profile_name)
    if hint:
        user_parts.append(hint)
    if example_frames:
        examples = {
            name: build_frame_inventory(name, frame, sample_rows=20)
            for name, frame in example_frames
        }
        user_parts.append(
            "Example integrated output (few-shot reference only):\n"
            f"{json.dumps(examples, ensure_ascii=False, indent=2)}"
        )
    if previous_errors:
        user_parts.append(
            "Previous plan failed validation. Fix these issues:\n"
            + "\n".join(f"- {err}" for err in previous_errors)
        )
    user_parts.append(
        "Return JSON with keys: operation, sources, group_keys, aggregations, "
        "renames, excluded_row_types, summary_row_labels, derived_rows, sort_by, "
        "blank_repeated_group_labels, group_display_column, column_order, output."
    )

    data = chat_json_fn(
        "\n\n".join(user_parts),
        system=system,
        base_url=base_url,
        model=model,
    )
    plan = ExecutionPlan.from_dict(data)
    return _sanitize_plan(plan, named_frames=named_frames, schemas=schemas)


def _sanitize_plan(
    plan: ExecutionPlan,
    *,
    named_frames: list[tuple[str, pd.DataFrame]],
    schemas: dict[str, FileSchema],
) -> ExecutionPlan:
    source_names = [name for name, _ in named_frames]
    if not plan.sources:
        plan.sources = list(source_names)
    else:
        known = set(source_names)
        plan.sources = [name for name in plan.sources if name in known] or list(source_names)

    if plan.operation not in SUPPORTED_OPERATIONS:
        plan.operation = "aggregate_merge"

    # Merge renames from schemas
    merged_renames: dict[str, str] = {}
    for schema in schemas.values():
        merged_renames.update(schema.column_renames)
    merged_renames.update(plan.renames)
    plan.renames = merged_renames

    if not plan.summary_row_labels:
        labels: list[str] = []
        for schema in schemas.values():
            for label in schema.summary_row_labels:
                if label not in labels:
                    labels.append(label)
        plan.summary_row_labels = labels

    if not plan.group_keys:
        # Prefer intersection of identifier columns across schemas (post-rename names)
        key_sets: list[set[str]] = []
        for schema in schemas.values():
            renamed = {
                schema.column_renames.get(col, col) for col in schema.identifier_columns
            }
            renamed |= set(schema.identifier_columns)
            key_sets.append(renamed)
        if key_sets:
            common = set.intersection(*key_sets) if len(key_sets) > 1 else key_sets[0]
            id_pref: list[str] = []
            for schema in schemas.values():
                for col in schema.identifier_columns:
                    name = schema.column_renames.get(col, col)
                    if name not in id_pref:
                        id_pref.append(name)
            plan.group_keys = [k for k in id_pref if k in common] or sorted(common)

    if not plan.aggregations:
        additive: list[str] = []
        for schema in schemas.values():
            for col in schema.additive_columns:
                name = schema.column_renames.get(col, col)
                if name not in additive and name not in plan.group_keys:
                    additive.append(name)
        plan.aggregations = {col: "sum" for col in additive}

    if not plan.excluded_row_types:
        plan.excluded_row_types = ["subtotal", "grand_total", "summary"]

    if not plan.group_display_column and plan.group_keys:
        # first non-identifier-looking label among keys / schema labels
        for schema in schemas.values():
            for col in schema.label_columns:
                name = schema.column_renames.get(col, col)
                if name not in plan.group_keys or len(plan.group_keys) > 1:
                    plan.group_display_column = name
                    break
            if plan.group_display_column:
                break
        if not plan.group_display_column:
            plan.group_display_column = plan.group_keys[0]

    if not plan.sheet_name_map:
        plan.sheet_name_map = {
            name: _default_sheet_name(name) for name in plan.sources
        }

    if not plan.column_order:
        order: list[str] = []
        for key in plan.group_keys:
            if key not in order:
                order.append(key)
        # labels not in keys
        for schema in schemas.values():
            for col in schema.label_columns:
                name = schema.column_renames.get(col, col)
                if name not in order:
                    order.append(name)
        for col in plan.aggregations:
            if col not in order:
                order.append(col)
        plan.column_order = order

    return plan


def _default_sheet_name(filename: str) -> str:
    stem = str(filename).rsplit("/", 1)[-1]
    if stem.lower().endswith((".xlsx", ".xlsm", ".xls")):
        stem = stem.rsplit(".", 1)[0]
    parts = stem.split("_")
    if len(parts) >= 3 and parts[0].isdigit():
        return "_".join(parts[1:-1]) or stem
    return stem[:31]
