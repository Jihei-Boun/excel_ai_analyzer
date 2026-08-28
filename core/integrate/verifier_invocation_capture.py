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
    if not capture_enabled():
        _last_record = record
        return None
    dest = capture_dir()
    if dest is None:
        _last_record = record
        return None
    try:
        dest.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = dest / f"verifier_invocations_{day}.jsonl"
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            _last_record = record
        return path
    except Exception:
        # Telemetry must not alter verifier semantics.
        _last_record = record
        return None


def get_last_record_for_tests() -> dict[str, Any] | None:
    return _last_record


def clear_last_record_for_tests() -> None:
    global _last_record
    _last_record = None


def env_case_id() -> str | None:
    raw = (os.environ.get("MULTI_VERIFIER_CAPTURE_CASE_ID") or "").strip()
    return raw or None


def env_request_id() -> str | None:
    raw = (os.environ.get("MULTI_VERIFIER_CAPTURE_REQUEST_ID") or "").strip()
    return raw or None


def update_last_escalation(
    *,
    escalation_triggered: bool,
    escalation_type: str | None,
) -> None:
    """Attach escalation outcome to the most recent capture (best-effort)."""
    global _last_record
    if _last_record is None:
        return
    try:
        updated = attach_response(
            _last_record,
            raw_text=_last_record.get("raw_model_response_text"),
            raw_parsed=_last_record.get("raw_model_response_parsed"),
            parsed_verdict=_last_record.get("parsed_verdict"),
            parsed_reason_code=_last_record.get("parsed_reason_code"),
            parsed_evidence=_last_record.get("parsed_evidence"),
            parse_ok=_last_record.get("parse_ok"),
            parse_error=_last_record.get("parse_error"),
            latency_s=_last_record.get("latency_s"),
            escalation_triggered=escalation_triggered,
            escalation_type=escalation_type,
        )
        updated["escalation_attached_at_utc"] = datetime.now(timezone.utc).strftime(
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
