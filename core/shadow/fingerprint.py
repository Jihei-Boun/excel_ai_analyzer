"""Structural result fingerprints — observation only, not correctness."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pandas as pd


def dataframe_fingerprint(df: pd.DataFrame | None) -> dict[str, Any] | None:
    if df is None:
        return None
    cols = [str(c) for c in df.columns.tolist()]
    dtypes = {str(c): str(df[c].dtype) for c in df.columns}
    nulls = {str(c): int(df[c].isna().sum()) for c in df.columns}
    # Stable hash over normalized CSV sample (bounded) — not semantic truth
    try:
        sample = df.head(50).copy()
        for c in sample.columns:
            sample[c] = sample[c].astype(str)
        payload = sample.to_csv(index=False)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    except Exception:  # noqa: BLE001
        digest = None
    return {
        "shape": [int(df.shape[0]), int(df.shape[1])],
        "columns": cols,
        "dtypes": dtypes,
        "null_counts": nulls,
        "content_hash_head50": digest,
    }


def structural_compare(
    legacy_fp: dict[str, Any] | None,
    shadow_fp: dict[str, Any] | None,
) -> str:
    """Objective metadata only — never declares semantic winner.

    - structurally_equal: shape + ordered columns + content hash match
    - structurally_similar: shape + column *set* match, content/order may differ
    - structurally_different: otherwise
    """
    if legacy_fp is None and shadow_fp is None:
        return "both_empty"
    if legacy_fp is None or shadow_fp is None:
        return "structurally_different"
    if (
        legacy_fp.get("shape") == shadow_fp.get("shape")
        and legacy_fp.get("columns") == shadow_fp.get("columns")
        and legacy_fp.get("content_hash_head50") == shadow_fp.get("content_hash_head50")
    ):
        return "structurally_equal"
    leg_cols = set(legacy_fp.get("columns") or [])
    sh_cols = set(shadow_fp.get("columns") or [])
    if legacy_fp.get("shape") == shadow_fp.get("shape") and leg_cols == sh_cols:
        return "structurally_similar"
    return "structurally_different"


def outcome_category(
    *,
    legacy_success: bool,
    shadow_success: bool,
    structural: str | None = None,
) -> str:
    if legacy_success and shadow_success:
        base = "legacy_success_shadow_success"
        if structural:
            return f"{base}_{structural}"
        return base
    if legacy_success and not shadow_success:
        return "legacy_success_shadow_failure"
    if not legacy_success and shadow_success:
        return "legacy_failure_shadow_success"
    return "legacy_failure_shadow_failure"
