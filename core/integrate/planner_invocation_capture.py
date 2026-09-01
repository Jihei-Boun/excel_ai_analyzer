"""Phase 39T — Planner invocation capture (observation only).

Captures exact planner inputs/outputs for diagnostics.
Does NOT alter planner input, model output, retries, or Legacy/Shadow results.

Enable with:
  MULTI_PLANNER_CAPTURE_ENABLED=true
  MULTI_PLANNER_CAPTURE_DIR=/path/to/dir

Default: OFF (no dir and no enabled flag).
Telemetry failures are swallowed.
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

_lock = threading.Lock()
_last_record: dict[str, Any] | None = None
_records_by_invocation: dict[str, dict[str, Any]] = {}


def capture_enabled() -> bool:
    raw = (os.environ.get("MULTI_PLANNER_CAPTURE_ENABLED") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return bool((os.environ.get("MULTI_PLANNER_CAPTURE_DIR") or "").strip())


def capture_dir() -> Path | None:
    raw = (os.environ.get("MULTI_PLANNER_CAPTURE_DIR") or "").strip()
    if not raw:
        return None
    return Path(raw)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonicalize_json(obj: Any) -> str:
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def env_request_id() -> str | None:
    return (
        os.environ.get("MULTI_PLANNER_CAPTURE_REQUEST_ID")
        or os.environ.get("MULTI_VERIFIER_CAPTURE_REQUEST_ID")
        or None
    )


def env_case_id() -> str | None:
    return (
        os.environ.get("MULTI_PLANNER_CAPTURE_CASE_ID")
        or os.environ.get("MULTI_VERIFIER_CAPTURE_CASE_ID")
        or None
    )


def infer_planner_type(*, model: str, retry_feedback: list[str] | None, parse_attempt: int) -> str:
    fb = "\n".join(retry_feedback or [])
    if "Semantic verifier verdict" in fb:
        return "SEMANTIC_ESCALATION_STRONG"
    if "Previous planning attempts with a smaller planner" in fb:
        return "FAILURE_ESCALATION_STRONG"
    if parse_attempt > 0:
        return "FORMAT_RETRY"
    if "32b" in str(model or "").lower():
        return "STRONG_INITIAL"
    return "FAST_INITIAL"


def classify_cannot_plan_subtype(plan: dict[str, Any] | None, *, parse_error: str | None) -> str | None:
    if parse_error:
        err = str(parse_error)
        if "Timeout" in err or "timeout" in err:
            return "BACKEND_FAILURE"
        return "FORMAT_EXHAUSTION"
    if not isinstance(plan, dict):
        return None
    if plan.get("status") != "cannot_plan":
        return None
    reason = str(plan.get("reason") or "")
    if reason == "planner_parse_failed":
        notes = " ".join(str(x) for x in (plan.get("notes") or []))
        if "Timeout" in notes or "timeout" in notes:
            return "BACKEND_FAILURE"
        return "FORMAT_EXHAUSTION"
    if reason == "non_numeric_additive_aggregation_unsupported":
        return "EXPLICIT_MODEL_CANNOT_PLAN"
    return "EXPLICIT_MODEL_CANNOT_PLAN"


def classify_raw_outcome(
    *,
    parse_ok: bool,
    plan: dict[str, Any] | None,
    backend_error: str | None,
) -> str:
    if backend_error:
        return "BACKEND_FAILURE"
    if not parse_ok:
        return "FORMAT_FAILURE"
    if isinstance(plan, dict) and plan.get("status") == "cannot_plan":
        return "DECLARED_CANNOT_PLAN"
    if isinstance(plan, dict) and plan.get("status") == "planned":
        return "PLAN_PARSED"
    return "UNKNOWN"


def new_invocation_id() -> str:
    return str(uuid.uuid4())


def persist_record(rec: dict[str, Any]) -> None:
    d = capture_dir()
    if d is None:
        return
    try:
        d.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = d / f"planner_invocations_{day}.jsonl"
        line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        with _lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        return


def record_planner_invocation(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    base_url: str,
    retry_feedback: list[str] | None,
    parse_attempt: int,
    raw_response: Any,
    parsed_plan: dict[str, Any] | None,
    parse_ok: bool,
    parse_error: str | None,
    backend_error: str | None,
    latency_s: float | None,
    temperature: float | None = None,
    timeout_s: float | None = 300.0,
) -> dict[str, Any] | None:
    """Observational capture. Never raises into planner control flow."""
    if not capture_enabled():
        return None
    try:
        inv_id = new_invocation_id()
        request_id = env_request_id()
        case_id = env_case_id()
        planner_type = infer_planner_type(
            model=model,
            retry_feedback=retry_feedback,
            parse_attempt=parse_attempt,
        )
        raw_text = (
            raw_response
            if isinstance(raw_response, str)
            else canonicalize_json(raw_response) if raw_response is not None else None
        )
        rec = {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "planner_invocation_id": inv_id,
            "request_id": request_id,
            "case_id": case_id,
            "planner_type": planner_type,
            "model": model,
            "base_url": base_url,
            "temperature": temperature,
            "timeout_s": timeout_s,
            "parse_attempt": parse_attempt,
            "system_prompt_hash": sha256_text(system_prompt or ""),
            "user_prompt_hash": sha256_text(user_prompt or ""),
            "retry_feedback": list(retry_feedback or []),
            "structured_input": {
                "system_prompt_hash": sha256_text(system_prompt or ""),
                "user_prompt": user_prompt,
            },
            "raw_model_response": raw_response,
            "raw_model_response_text": raw_text,
            "extracted_json": raw_response if isinstance(raw_response, dict) else None,
            "parse_ok": parse_ok,
            "parse_error": parse_error,
            "backend_error": backend_error,
            "parsed_plan": parsed_plan,
            "raw_outcome": classify_raw_outcome(
                parse_ok=parse_ok,
                plan=parsed_plan,
                backend_error=backend_error,
            ),
            "cannot_plan_subtype": classify_cannot_plan_subtype(
                parsed_plan, parse_error=parse_error or backend_error
            ),
            "latency_s": latency_s,
            "exact_payload_hash": sha256_text(
                canonicalize_json(
                    {
                        "system": system_prompt,
                        "user": user_prompt,
                        "model": model,
                        "retry_feedback": retry_feedback or [],
                    }
                )
            ),
            "captured_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        persist_record(rec)
        with _lock:
            global _last_record
            _last_record = rec
            _records_by_invocation[inv_id] = rec
        return rec
    except Exception:
        return None


def get_last_record_for_tests() -> dict[str, Any] | None:
    return _last_record


def clear_last_record_for_tests() -> None:
    global _last_record
    with _lock:
        _last_record = None
        _records_by_invocation.clear()
