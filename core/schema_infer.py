"""LLM 기반 범용 스키마 추론 (열 이름 하드코딩 없음)."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from core.llm_client import chat_json
from core.plan_types import FileSchema
from core.profile_loader import active_profile
from core.text_normalize import normalize_text


def build_frame_inventory(
    name: str,
    df: pd.DataFrame,
    *,
    sample_rows: int = 12,
) -> dict[str, Any]:
    """LLM에 넘길 파일 구조 요약 (결정론적 관측만)."""
    columns: list[dict[str, Any]] = []
    for col in df.columns:
        series = df[col]
        non_null = series.dropna()
        sample = [_jsonable(v) for v in non_null.head(8).tolist()]
        columns.append(
            {
                "name": str(col),
                "dtype": str(series.dtype),
                "null_ratio": float(series.isna().mean()) if len(series) else 0.0,
                "nunique": int(non_null.nunique()),
                "sample_values": sample,
                "is_numeric_like": bool(
                    pd.api.types.is_numeric_dtype(series)
                    or _mostly_numeric(non_null)
                ),
            }
        )

    label_like_cols = [
        str(c)
        for c in df.columns
        if not pd.api.types.is_numeric_dtype(df[c])
    ]
    row_label_samples: list[str] = []
    for col in label_like_cols[:3]:
        for value in df[col].dropna().astype(str).head(40):
            text = str(value).strip()
            if text and text not in row_label_samples:
                row_label_samples.append(text)
            if len(row_label_samples) >= 40:
                break

    return {
        "source": name,
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "columns": columns,
        "head_rows": _rows_as_records(df.head(sample_rows)),
        "tail_rows": _rows_as_records(df.tail(min(sample_rows, len(df)))),
        "distinct_row_labels_sample": row_label_samples[:40],
    }


def semantic_hints_text(
    *,
    profile_name: str | None = None,
) -> str:
    """프로필의 semantic_hints를 프롬프트 텍스트로 변환 (강제 규칙 아님)."""
    profile = active_profile(
        profile_name=profile_name,
    )
    hints = profile.get("semantic_hints")
    if not hints:
        return ""
    return (
        "Optional domain hints (do NOT hardcode these names; use only if they fit "
        "the actual columns/rows):\n"
        f"{json.dumps(hints, ensure_ascii=False, indent=2)}"
    )


def infer_file_schema(
    name: str,
    df: pd.DataFrame,
    *,
    base_url: str,
    model: str,
    profile_name: str | None = None,
    example_inventory: dict[str, Any] | None = None,
    chat_json_fn=chat_json,
) -> FileSchema:
    """단일 파일 스키마를 LLM이 JSON으로 추론한다."""
    inventory = build_frame_inventory(name, df)
    system = (
        "You analyze tabular Excel data loaded into pandas. "
        "Infer a structural schema. Return ONLY a JSON object. "
        "Never invent columns that do not exist. "
        "Column names in the output must come from the provided inventory "
        "(before renames) or be explicit rename targets. "
        "Identify which columns are identifiers/keys, which are descriptive labels, "
        "which numeric columns are safely additive (summable), and which are not "
        "(ratios, percentages, dates, ids). "
        "Detect summary/total row labels that appear in the data (subtotal, grand total, "
        "footer summaries). Normalize label spelling by collapsing spaces when listing them. "
        "If a column is misnamed (e.g. a code stored under a name column), put a "
        "column_renames map from current name to a clearer canonical name for THIS job only."
    )
    hint_block = semantic_hints_text(profile_name=profile_name)
    user_parts = [
        f"File inventory:\n{json.dumps(inventory, ensure_ascii=False, indent=2)}",
    ]
    if hint_block:
        user_parts.append(hint_block)
    if example_inventory:
        user_parts.append(
            "Reference integrated example inventory (for structure only; "
            "do not copy domain names unless present in the source):\n"
            f"{json.dumps(example_inventory, ensure_ascii=False, indent=2)}"
        )
    user_parts.append(
        "Return JSON with keys: header_rows, identifier_columns, label_columns, "
        "additive_columns, non_additive_columns, summary_row_labels, column_renames, notes. "
        "header_rows must be a JSON array of integers (e.g. [0] or [0, 1]), never objects."
    )
    data = chat_json_fn(
        "\n\n".join(user_parts),
        system=system,
        base_url=base_url,
        model=model,
    )
    schema = FileSchema.from_dict(data, source=name)
    return _sanitize_schema_against_frame(schema, df)


def infer_schemas(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    base_url: str,
    model: str,
    profile_name: str | None = None,
    example_frames: list[tuple[str, pd.DataFrame]] | None = None,
    chat_json_fn=chat_json,
) -> dict[str, FileSchema]:
    example_inventory = None
    if example_frames:
        ex_name, ex_df = example_frames[0]
        example_inventory = build_frame_inventory(ex_name, ex_df)

    schemas: dict[str, FileSchema] = {}
    for name, frame in named_frames:
        schemas[name] = infer_file_schema(
            name,
            frame,
            base_url=base_url,
            model=model,
            profile_name=profile_name,
            example_inventory=example_inventory,
            chat_json_fn=chat_json_fn,
        )
    return schemas


def _sanitize_schema_against_frame(schema: FileSchema, df: pd.DataFrame) -> FileSchema:
    cols = {str(c) for c in df.columns}

    def _keep(names: list[str]) -> list[str]:
        return [name for name in names if name in cols]

    renames = {
        src: dst
        for src, dst in schema.column_renames.items()
        if src in cols and dst
    }
    # After rename, identifier/additive lists may use either side
    id_cols = _keep(schema.identifier_columns)
    for src, dst in renames.items():
        if src in schema.identifier_columns and dst not in id_cols:
            # keep source name; engine applies renames first
            if src not in id_cols:
                id_cols.append(src)

    additive = _keep(schema.additive_columns)
    if not additive:
        additive = [
            str(c)
            for c in df.columns
            if pd.api.types.is_numeric_dtype(df[c])
        ]

    labels = _keep(schema.label_columns)
    non_add = _keep(schema.non_additive_columns)

    summary_labels = []
    for label in schema.summary_row_labels:
        cleaned = _collapse_spaces(label)
        if cleaned and cleaned not in summary_labels:
            summary_labels.append(cleaned)

    if not summary_labels:
        summary_labels = _guess_summary_labels(df)

    if not id_cols:
        id_cols = _guess_identifier_columns(df, additive)

    return FileSchema(
        source=schema.source,
        header_rows=schema.header_rows or [0],
        identifier_columns=id_cols,
        label_columns=labels,
        additive_columns=additive,
        non_additive_columns=non_add,
        summary_row_labels=summary_labels,
        column_renames=renames,
        notes=list(schema.notes),
    )


def _guess_summary_labels(df: pd.DataFrame) -> list[str]:
    """휴리스틱 보조 — LLM 실패 시에만 쓰이며 도메인 키워드를 강제하지 않는다."""
    candidates: list[str] = []
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        for value in df[col].dropna().astype(str):
            text = _collapse_spaces(value)
            norm = normalize_text(text)
            if not text:
                continue
            if any(token in norm for token in ("소계", "합계", "총계", "subtotal", "total")):
                if text not in candidates:
                    candidates.append(text)
    return candidates[:12]


def _guess_identifier_columns(df: pd.DataFrame, additive: list[str]) -> list[str]:
    scored: list[tuple[float, str]] = []
    additive_set = set(additive)
    for col in df.columns:
        if str(col) in additive_set:
            continue
        series = df[col]
        non_null = series.dropna()
        if non_null.empty:
            continue
        ratio = float(non_null.nunique() / max(len(non_null), 1))
        scored.append((ratio, str(col)))
    scored.sort(reverse=True)
    return [name for _, name in scored[:2]]


def _collapse_spaces(value: object) -> str:
    return " ".join(str(value or "").split())


def _mostly_numeric(series: pd.Series) -> bool:
    if series.empty:
        return False
    coerced = pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    )
    return float(coerced.notna().mean()) >= 0.7


def _rows_as_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in df.itertuples(index=False):
        item = {}
        for col, value in zip(df.columns, row):
            item[str(col)] = _jsonable(value)
        records.append(item)
    return records


def _jsonable(value: object) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if pd.isna(value):
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)
