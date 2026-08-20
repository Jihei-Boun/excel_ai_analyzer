"""Append-only JSONL telemetry sink for Shadow observations."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_write_lock = threading.Lock()


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_telemetry_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def append_telemetry_record(telemetry_dir: Path, record: dict[str, Any]) -> Path:
    """Write one JSONL line. Never raises into caller (best-effort)."""
    try:
        ensure_telemetry_dir(telemetry_dir)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        out = telemetry_dir / f"shadow_{day}.jsonl"
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _write_lock:
            with out.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return out
    except Exception:  # noqa: BLE001
        return telemetry_dir / "shadow_write_failed.jsonl"


def new_base_record(
    *,
    schema_version: int,
    pipeline_version: str,
    request_id: str,
    shadow_request_id: str,
) -> dict[str, Any]:
    return {
        "shadow_schema_version": schema_version,
        "pipeline_version": pipeline_version,
        "request_id": request_id,
        "shadow_request_id": shadow_request_id,
        "recorded_at_utc": _utc_stamp(),
    }
