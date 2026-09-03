"""Request-scoped artifact materialization.

Production routers may write charts/workbooks to shared export directories.
This module copies those existing bytes into the caller-provided output
directory. It does not regenerate charts or re-run export pipelines.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from core.application.contracts import PREVIEW_ROW_LIMIT, Artifact, ContractError
from core.io.export_utils import dataframe_to_xlsx_bytes, safe_download_name

_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MEDIA_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".json": "application/json",
}


def sanitize_request_dirname(request_id: str) -> str:
    """Turn a caller request_id into a single path component.

    The raw request_id is never used as a filesystem path.
    """
    raw = str(request_id or "")
    cleaned = _UNSAFE_FILENAME_RE.sub("_", raw).strip("._")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return cleaned[:64]


def sanitize_filename(name: str, *, default: str = "artifact.bin") -> str:
    text = str(name or "").strip() or default
    text = Path(text).name
    for ch in '\\/:*?"<>|\x00':
        text = text.replace(ch, "_")
    text = text.strip(" .")
    if not text or text in {".", ".."}:
        return default
    return text[:180]


def resolve_output_root(output_directory: str | Path) -> Path:
    raw = str(output_directory or "").strip()
    if not raw:
        raise ContractError(
            "invalid_output_directory",
            "output_directory is required.",
        )
    path = Path(raw)
    if not path.is_absolute():
        raise ContractError(
            "invalid_output_directory",
            "output_directory must be an absolute path.",
        )
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise ContractError(
            "invalid_output_directory",
            "output_directory could not be resolved.",
        ) from exc
    if resolved.exists() and not resolved.is_dir():
        raise ContractError(
            "invalid_output_directory",
            "output_directory exists and is not a directory.",
        )
    return resolved


def request_output_dir(output_directory: str | Path, request_id: str) -> Path:
    root = resolve_output_root(output_directory)
    scoped = (root / sanitize_request_dirname(request_id)).resolve()
    _assert_inside(root, scoped)
    scoped.mkdir(parents=True, exist_ok=True)
    return scoped


def _assert_inside(root: Path, candidate: Path) -> None:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ContractError(
            "invalid_output_directory",
            "artifact path escaped the request output directory.",
            stage="artifact",
        ) from exc


def media_type_for(path: Path) -> str:
    return _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_safe(value: Any) -> Any:
    """Convert pandas/numpy/path values into JSON-serializable data."""
    if value is None:
        return None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            return None
        return value
    if isinstance(value, (datetime, date, pd.Timestamp)):
        try:
            return pd.Timestamp(value).isoformat()
        except Exception:
            return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return None
    if isinstance(value, pd.DataFrame):
        return None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except Exception:
            pass
    return str(value)


def dataframe_preview(df: pd.DataFrame | None, *, limit: int = PREVIEW_ROW_LIMIT) -> dict[str, Any]:
    if df is None:
        return {"shape": [0, 0], "columns": [], "preview_records": []}
    columns = [str(column) for column in df.columns]
    preview = df.head(max(0, int(limit))).replace({pd.NA: None})
    records = json_safe(preview.to_dict(orient="records"))
    if not isinstance(records, list):
        records = []
    return {
        "shape": [int(df.shape[0]), int(df.shape[1])],
        "columns": columns,
        "preview_records": records,
    }


def write_bytes_artifact(
    dest_dir: Path,
    filename: str,
    payload: bytes,
    *,
    kind: str,
    artifact_id: str,
) -> Artifact:
    dest_dir = dest_dir.resolve()
    dest = dest_dir / sanitize_filename(filename)
    _assert_inside(dest_dir, dest)
    dest.write_bytes(payload)
    return Artifact(
        artifact_id=artifact_id,
        kind=kind,
        path=str(dest),
        media_type=media_type_for(dest),
        filename=dest.name,
        size_bytes=dest.stat().st_size,
        sha256=sha256_file(dest),
    )


def write_dataframe_xlsx(
    dest_dir: Path,
    df: pd.DataFrame,
    *,
    filename: str = "result.xlsx",
    artifact_id: str = "result-table",
) -> Artifact:
    payload = dataframe_to_xlsx_bytes(df)
    return write_bytes_artifact(
        dest_dir,
        safe_download_name(filename, default="result.xlsx"),
        payload,
        kind="table",
        artifact_id=artifact_id,
    )


def materialize_existing_file(
    dest_dir: Path,
    source: str | Path,
    *,
    filename: str | None = None,
    kind: str,
    artifact_id: str,
) -> Artifact | None:
    path = Path(str(source))
    try:
        if not path.is_file():
            return None
    except OSError:
        return None
    dest_name = sanitize_filename(filename or path.name, default=path.name)
    dest = dest_dir.resolve() / dest_name
    _assert_inside(dest_dir.resolve(), dest)
    if path.resolve() != dest:
        shutil.copy2(path, dest)
    return Artifact(
        artifact_id=artifact_id,
        kind=kind,
        path=str(dest),
        media_type=media_type_for(dest),
        filename=dest.name,
        size_bytes=dest.stat().st_size,
        sha256=sha256_file(dest),
    )


def write_manifest(dest_dir: Path, artifacts: list[Artifact]) -> Path:
    payload = {
        "artifacts": [item.to_dict() for item in artifacts],
    }
    path = dest_dir / "artifact_manifest.json"
    _assert_inside(dest_dir.resolve(), path.resolve())
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
