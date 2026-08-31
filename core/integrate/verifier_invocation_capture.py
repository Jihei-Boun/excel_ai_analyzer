"""Phase 39L — Semantic Verifier invocation capture (observation only).

Captures the exact pre-model verifier input for replay/diagnostics.
Does NOT alter semantic judgments. Does NOT infer hallucination.

Enable with env MULTI_VERIFIER_CAPTURE_DIR=/path/to/dir
Optional: MULTI_VERIFIER_CAPTURE_ENABLED=true|false (default true when dir set)
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CAPTURE_SCHEMA_VERSION = 1
MATERIALIZATION_DEFAULT = "final_schema_expr_partition"

_lock = threading.Lock()
_last_record: dict[str, Any] | None = None
_records_by_attempt: dict[str, dict[str, Any]] = {}
_records_by_invocation: dict[str, dict[str, Any]] = {}
_integrity_failures: list[dict[str, Any]] = []


def capture_enabled() -> bool:
    raw = (os.environ.get("MULTI_VERIFIER_CAPTURE_ENABLED") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return bool((os.environ.get("MULTI_VERIFIER_CAPTURE_DIR") or "").strip())


def capture_dir() -> Path | None:
    raw = (os.environ.get("MULTI_VERIFIER_CAPTURE_DIR") or "").strip()
    if not raw:
        return None
    return Path(raw)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonicalize_json(obj: Any) -> str:
    """Conservative canonical JSON: UTF-8, sorted keys, compact separators.

    Does not drop fields, rewrite evidence, or invent equivalence.
    """
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def prompt_template_hash(system_prompt: str, user_instruction_prefix: str) -> str:
    """Hash static instruction content (system + fixed user prefix before payload)."""
    blob = system_prompt + "\n---\n" + user_instruction_prefix
    return sha256_text(blob)


def build_invocation_record(
    *,
    verbatim_user_message: str,
    system_prompt: str,
    user_instruction_prefix: str,
    structured_payload: dict[str, Any],
    model: str,
    base_url: str,
    timeout_s: int | None,
    temperature: float | None,
    format_json: bool | None,
    materialization_mode: str,
    variant: str,
    independent: bool,
    retry_attempt: int = 0,
    invocation_type: str = "semantic_verifier",
    request_id: str | None = None,
    case_id: str | None = None,
    result_provided: bool,
    chat_path: str,
    # Phase 39O — attempt lineage (optional; null for historical / absent)
    attempt_id: str | None = None,
    plan_fingerprint: str | None = None,
    result_fingerprint: str | None = None,
    planner_model: str | None = None,
    attempt_stage: str | None = None,
    parent_attempt_id: str | None = None,
    escalation_trigger: str | None = None,
    became_final: bool | None = None,
    final_attempt_id: str | None = None,
    attempt_disposition: str | None = None,
    lineage_schema_version: int | None = None,
) -> dict[str, Any]:
    """Assemble a capture record. Does not write to disk."""
    exact_input = {
        "system": system_prompt,
        "user": verbatim_user_message,
    }
    exact_serialized = canonicalize_json(exact_input)
    # Raw fingerprint of exact bytes as submitted conceptually (system+user).
    raw_payload_hash = sha256_text(exact_serialized)
    canonical_payload_hash = sha256_text(canonicalize_json(structured_payload))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "verifier_invocation_id": str(uuid.uuid4()),
        "request_id": request_id,
        "case_id": case_id,
        "captured_at_utc": now,
        "verifier_stage": "pre_model_invocation",
        "invocation_type": invocation_type,
        "model_id": model,
        "backend_path": chat_path,
        "base_url": base_url,
        "materialization_version": materialization_mode,
        "prompt_version_hash": prompt_template_hash(
            system_prompt, user_instruction_prefix
        ),
        "variant": variant,
        "independent": independent,
        "temperature": temperature,
        "format_json": format_json,
        "timeout_s": timeout_s,
        "retry_attempt": retry_attempt,
        "result_provided": result_provided,
        # Phase 39O lineage (absent => null; do not invent historical IDs)
        "attempt_id": attempt_id,
        "plan_fingerprint": plan_fingerprint,
        "result_fingerprint": result_fingerprint,
        "planner_model": planner_model,
        "attempt_stage": attempt_stage,
        "parent_attempt_id": parent_attempt_id,
        "escalation_trigger": escalation_trigger,
        "became_final": became_final,
        "final_attempt_id": final_attempt_id,
        "attempt_disposition": attempt_disposition,
        "lineage_schema_version": lineage_schema_version,
        "exact_verifier_input": exact_input,
        "exact_payload_hash": raw_payload_hash,
        "canonical_structured_payload": structured_payload,
        "canonical_payload_hash": canonical_payload_hash,
        "deterministic_evidence_snapshot": structured_payload.get(
            "materialization_evidence"
        ),
        "raw_model_response_text": None,
        "raw_model_response_parsed": None,
        "parsed_verdict": None,
        "parsed_reason_code": None,
        "parsed_evidence": None,
        "parse_ok": None,
        "parse_error": None,
        "escalation_triggered": None,
        "escalation_type": None,
        "latency_s": None,
        "capture_notes": [
            "exact_verifier_input is the system+user messages at capture point",
            "canonical_structured_payload is the JSON object embedded in the user message",
            "Python does not judge whether model claims are hallucinated",
            "attempt_id links this invocation to the candidate attempt evaluated",
        ],
    }


def attach_response(
    record: dict[str, Any],
    *,
    raw_text: str | None,
    raw_parsed: dict[str, Any] | None,
    parsed_verdict: str | None,
    parsed_reason_code: str | None,
    parsed_evidence: list[str] | None,
    parse_ok: bool | None,
    parse_error: str | None,
    latency_s: float | None,
    escalation_triggered: bool | None = None,
    escalation_type: str | None = None,
) -> dict[str, Any]:
    out = dict(record)
    out["raw_model_response_text"] = raw_text
    out["raw_model_response_parsed"] = raw_parsed
    out["parsed_verdict"] = parsed_verdict
    out["parsed_reason_code"] = parsed_reason_code
    out["parsed_evidence"] = parsed_evidence
    out["parse_ok"] = parse_ok
    out["parse_error"] = parse_error
    out["latency_s"] = latency_s
    out["escalation_triggered"] = escalation_triggered
    out["escalation_type"] = escalation_type
    out["post_model_stage"] = "response_attached"
    return out


def persist_record(record: dict[str, Any]) -> Path | None:
    """Append JSONL under capture dir. Never raises into caller semantics."""
    global _last_record
    _index_record(record)
    if not capture_enabled():
        return None
    dest = capture_dir()
    if dest is None:
        return None
    try:
        dest.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = dest / f"verifier_invocations_{day}.jsonl"
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        return path
    except Exception:
        # Telemetry must not alter verifier semantics.
        return None


def _index_record(record: dict[str, Any]) -> None:
    """Identity-keyed index. Does not rebind a record onto another request."""
    global _last_record
    with _lock:
        _last_record = record
        iid = record.get("verifier_invocation_id")
        if isinstance(iid, str) and iid:
            _records_by_invocation[iid] = record
        aid = record.get("attempt_id")
        if isinstance(aid, str) and aid:
            _records_by_attempt[aid] = record


def get_last_record_for_tests() -> dict[str, Any] | None:
    return _last_record


def get_record_for_attempt(attempt_id: str | None) -> dict[str, Any] | None:
    if not attempt_id:
        return None
    with _lock:
        rec = _records_by_attempt.get(str(attempt_id))
        return rec


def get_integrity_failures_for_tests() -> list[dict[str, Any]]:
    with _lock:
        return list(_integrity_failures)


def clear_last_record_for_tests() -> None:
    global _last_record
    with _lock:
        _last_record = None
        _records_by_attempt.clear()
        _records_by_invocation.clear()
        _integrity_failures.clear()


def env_case_id() -> str | None:
    raw = (os.environ.get("MULTI_VERIFIER_CAPTURE_CASE_ID") or "").strip()
    return raw or None


def env_request_id() -> str | None:
    raw = (os.environ.get("MULTI_VERIFIER_CAPTURE_REQUEST_ID") or "").strip()
    return raw or None


def _record_matches(
    rec: dict[str, Any] | None,
    *,
    request_id: str | None,
    attempt_id: str | None,
) -> bool:
    if rec is None:
        return False
    if attempt_id is not None:
        existing = rec.get("attempt_id")
        if existing is not None and existing != attempt_id:
            return False
    if request_id is not None:
        existing_rid = rec.get("request_id")
        if existing_rid is not None and existing_rid != request_id:
            return False
    return True


def _resolve_record_locked(
    *,
    request_id: str | None,
    attempt_id: str | None,
) -> dict[str, Any] | None:
    if attempt_id:
        rec = _records_by_attempt.get(str(attempt_id))
        if rec is not None:
            return rec
    if request_id is None and attempt_id is None:
        return _last_record
    if _last_record is not None and _record_matches(
        _last_record, request_id=request_id, attempt_id=attempt_id
    ):
        return _last_record
    return None


def _refuse_rebind(
    *,
    operation: str,
    rec: dict[str, Any],
    request_id: str | None,
    attempt_id: str | None,
) -> None:
    _integrity_failures.append(
        {
            "event": "provenance_integrity_failure",
            "operation": operation,
            "action": "refused_rebind",
            "existing_request_id": rec.get("request_id"),
            "existing_attempt_id": rec.get("attempt_id"),
            "expected_request_id": request_id,
            "expected_attempt_id": attempt_id,
        }
    )


def update_last_escalation(
    *,
    escalation_triggered: bool,
    escalation_type: str | None,
    request_id: str | None = None,
    attempt_id: str | None = None,
) -> None:
    """Attach escalation outcome to the capture for this request/attempt.

    If identity is provided and does not match the stored record, refuse
    to rebind. Does not silently rewrite A into B.
    """
    try:
        with _lock:
            rec = _resolve_record_locked(request_id=request_id, attempt_id=attempt_id)
            if rec is None:
                return
            if (request_id is not None or attempt_id is not None) and not _record_matches(
                rec, request_id=request_id, attempt_id=attempt_id
            ):
                _refuse_rebind(
                    operation="update_last_escalation",
                    rec=rec,
                    request_id=request_id,
                    attempt_id=attempt_id,
                )
                return
            updated = attach_response(
                rec,
                raw_text=rec.get("raw_model_response_text"),
                raw_parsed=rec.get("raw_model_response_parsed"),
                parsed_verdict=rec.get("parsed_verdict"),
                parsed_reason_code=rec.get("parsed_reason_code"),
                parsed_evidence=rec.get("parsed_evidence"),
                parse_ok=rec.get("parse_ok"),
                parse_error=rec.get("parse_error"),
                latency_s=rec.get("latency_s"),
                escalation_triggered=escalation_triggered,
                escalation_type=escalation_type,
            )
            updated["escalation_attached_at_utc"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
        persist_record(updated)
    except Exception:
        return


def update_last_lineage_finalization(
    *,
    became_final: bool | None,
    final_attempt_id: str | None,
    attempt_disposition: str | None = None,
    request_id: str | None = None,
    attempt_id: str | None = None,
) -> None:
    """Append-only finalization linkage for the capture of this attempt.

    Does not rewrite plan_fingerprint / attempt_id (attempt immutability).
    Does not look up "latest" when identity is provided.
    """
    try:
        with _lock:
            rec = _resolve_record_locked(request_id=request_id, attempt_id=attempt_id)
            if rec is None:
                return
            if (request_id is not None or attempt_id is not None) and not _record_matches(
                rec, request_id=request_id, attempt_id=attempt_id
            ):
                _refuse_rebind(
                    operation="update_last_lineage_finalization",
                    rec=rec,
                    request_id=request_id,
                    attempt_id=attempt_id,
                )
                return
            updated = dict(rec)
            if became_final is not None:
                updated["became_final"] = became_final
            if final_attempt_id is not None:
                updated["final_attempt_id"] = final_attempt_id
            if attempt_disposition is not None:
                updated["attempt_disposition"] = attempt_disposition
            updated["lineage_finalized_at_utc"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
        persist_record(updated)
    except Exception:
        return


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def classify_replay_fidelity(
    *,
    source_exact_payload_hash: str | None,
    replay_exact_payload_hash: str | None,
    source_canonical_payload_hash: str | None,
    replay_canonical_payload_hash: str | None,
    used_captured_verbatim_user: bool,
    reconstructed_from_plan: bool,
) -> str:
    """Return EXACT_REPLAY | CANONICAL_EQUIVALENT_REPLAY | RECONSTRUCTED_REPLAY."""
    if reconstructed_from_plan and not used_captured_verbatim_user:
        return "RECONSTRUCTED_REPLAY"
    if (
        source_exact_payload_hash
        and replay_exact_payload_hash
        and source_exact_payload_hash == replay_exact_payload_hash
        and used_captured_verbatim_user
    ):
        return "EXACT_REPLAY"
    if (
        source_canonical_payload_hash
        and replay_canonical_payload_hash
        and source_canonical_payload_hash == replay_canonical_payload_hash
    ):
        return "CANONICAL_EQUIVALENT_REPLAY"
    return "RECONSTRUCTED_REPLAY"
