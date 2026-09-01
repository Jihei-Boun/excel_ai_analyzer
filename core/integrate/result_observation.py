"""Phase 39Z — bounded deterministic result observation for the semantic verifier.

Python exposes facts only. No semantic judgment (join vs union, grouping, sides).
Fail-open: observation errors return None so the verifier still runs.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

# Conservative bounds: match Phase 39Y research (5 rows / 24 columns) and cap
# serialized evidence so the verifier prompt cannot grow without limit.
# 4000 chars is roughly a thousand tokens worst-case and still includes
# mandatory row_count + column names after sample truncation.
MAX_RESULT_SAMPLE_ROWS = 5
MAX_RESULT_SAMPLE_COLUMNS = 24
MAX_RESULT_SERIALIZED_CHARS = 4000


def observe_result_for_verifier(result: Any) -> dict[str, Any] | None:
    """Build a request-local bounded observation. Never mutates ``result``."""
    try:
        return _observe_unchecked(result)
    except Exception:  # noqa: BLE001
        return None


def _observe_unchecked(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    if isinstance(result, dict) and (
        "row_count" in result or result.get("kind") in {"dataframe", "scalar", "records", "empty"}
    ):
        return _bound_dict(result)
    try:
        import pandas as pd
    except Exception:  # noqa: BLE001
        pd = None  # type: ignore[assignment]
    if pd is not None and isinstance(result, pd.DataFrame):
        return _observe_dataframe(result)
    if pd is not None and isinstance(result, pd.Series):
        return _observe_dataframe(result.to_frame())
    if isinstance(result, list):
        return _observe_records(result)
    if isinstance(result, dict):
        return {
            "kind": "mapping",
            "row_count": 1,
            "column_count": len(result),
            "columns": [str(k) for k in list(result.keys())[:MAX_RESULT_SAMPLE_COLUMNS]],
            "sample_rows": [_sanitize_row({str(k): v for k, v in list(result.items())[:MAX_RESULT_SAMPLE_COLUMNS]})],
            "truncated": len(result) > MAX_RESULT_SAMPLE_COLUMNS,
            "truncated_rows": False,
            "truncated_columns": len(result) > MAX_RESULT_SAMPLE_COLUMNS,
        }
    return {
        "kind": "scalar",
        "row_count": 1,
        "column_count": 1,
        "columns": ["value"],
        "sample_rows": [{"value": _jsonable(result)}],
        "truncated": False,
        "truncated_rows": False,
        "truncated_columns": False,
        "value": _jsonable(result),
    }


def _observe_dataframe(df: Any) -> dict[str, Any]:
    n_rows = int(df.shape[0])
    n_cols = int(df.shape[1])
    all_cols = [str(c) for c in df.columns]
    col_trunc = n_cols > MAX_RESULT_SAMPLE_COLUMNS
    cols = all_cols[:MAX_RESULT_SAMPLE_COLUMNS]
    row_trunc = n_rows > MAX_RESULT_SAMPLE_ROWS
    sample_df = df.head(MAX_RESULT_SAMPLE_ROWS).copy()
    if col_trunc:
        sample_df = sample_df.iloc[:, :MAX_RESULT_SAMPLE_COLUMNS].copy()
    records = sample_df.to_dict(orient="records")
    sample_rows = [_sanitize_row(r) for r in records]
    obs = {
        "kind": "dataframe",
        "row_count": n_rows,
        "column_count": n_cols,
        "columns": cols,
        "sample_rows": sample_rows,
        "truncated": bool(row_trunc or col_trunc),
        "truncated_rows": bool(row_trunc),
        "truncated_columns": bool(col_trunc),
    }
    return _enforce_size(obs)


def _observe_records(rows: list[Any]) -> dict[str, Any]:
    n = len(rows)
    first = rows[0] if rows and isinstance(rows[0], dict) else {}
    cols = [str(k) for k in list(first.keys())[:MAX_RESULT_SAMPLE_COLUMNS]] if first else []
    sample = []
    for row in rows[:MAX_RESULT_SAMPLE_ROWS]:
        if isinstance(row, dict):
            sample.append(_sanitize_row({c: row.get(c) for c in cols}))
    obs = {
        "kind": "records",
        "row_count": n,
        "column_count": len(cols),
        "columns": cols,
        "sample_rows": sample,
        "truncated": n > MAX_RESULT_SAMPLE_ROWS,
        "truncated_rows": n > MAX_RESULT_SAMPLE_ROWS,
        "truncated_columns": False,
    }
    return _enforce_size(obs)


def _bound_dict(d: dict[str, Any]) -> dict[str, Any]:
    cols = [str(c) for c in list(d.get("columns") or [])][:MAX_RESULT_SAMPLE_COLUMNS]
    sample = list(d.get("sample_rows") or [])[:MAX_RESULT_SAMPLE_ROWS]
    sample = [_sanitize_row(r) if isinstance(r, dict) else r for r in sample]
    obs = {
        "kind": str(d.get("kind") or "dataframe"),
        "row_count": d.get("row_count"),
        "column_count": d.get("column_count", len(cols)),
        "columns": cols,
        "sample_rows": sample,
        "truncated": bool(d.get("truncated")) or len(list(d.get("columns") or [])) > MAX_RESULT_SAMPLE_COLUMNS
        or len(list(d.get("sample_rows") or [])) > MAX_RESULT_SAMPLE_ROWS,
        "truncated_rows": bool(d.get("truncated_rows")),
        "truncated_columns": bool(d.get("truncated_columns")),
    }
    if "value" in d:
        obs["value"] = _jsonable(d.get("value"))
    return _enforce_size(obs)


def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {str(k): _jsonable(v) for k, v in row.items()}


def _jsonable(v: Any) -> Any:
    if v is None:
        return None
    try:
        if v != v:  # NaN
            return None
    except Exception:
        pass
    try:
        import pandas as pd

        if v is pd.NA:
            return None
        if isinstance(v, pd.Timestamp):
            return v.isoformat()
    except Exception:
        pass
    if isinstance(v, bool):
        return bool(v)
    if isinstance(v, (int, str)):
        return v
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return float(v)
    if hasattr(v, "item"):
        try:
            return _jsonable(v.item())
        except Exception:
            return str(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")[:120]
    s = str(v)
    if " at 0x" in s:
        return s.split(" at 0x")[0]
    return s[:240]


def _content_hash(obs: dict[str, Any]) -> str:
    head = json.dumps(
        {
            "row_count": obs.get("row_count"),
            "columns": obs.get("columns"),
            "sample": obs.get("sample_rows"),
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(head.encode("utf-8")).hexdigest()[:16]


def _enforce_size(obs: dict[str, Any]) -> dict[str, Any]:
    obs = dict(obs)
    obs["content_hash"] = _content_hash(obs)
    blob = json.dumps(obs, ensure_ascii=False, default=str)
    sample = list(obs.get("sample_rows") or [])
    while len(blob) > MAX_RESULT_SERIALIZED_CHARS and sample:
        sample = sample[:-1]
        obs["sample_rows"] = sample
        obs["truncated"] = True
        obs["truncated_rows"] = True
        obs["content_hash"] = _content_hash(obs)
        blob = json.dumps(obs, ensure_ascii=False, default=str)
    if len(blob) > MAX_RESULT_SERIALIZED_CHARS:
        obs["sample_rows"] = []
        obs["truncated"] = True
        obs["size_truncated"] = True
        obs["content_hash"] = _content_hash(obs)
    return obs
