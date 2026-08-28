"""Phase 39O — Candidate attempt lineage (observation / provenance only).

Links verifier invocations to the exact candidate attempt they evaluated.
Does NOT alter planner, verifier, executor, or escalation semantics.
Telemetry failures must never change semantic outcomes.
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.integrate.integration_plan_types import (
    IntegrationPlan,
    canonical_integration_plan_signature,
)

# --- Disposition (pipeline history, not manual correctness) ---
DISPOSITION_FINAL = "final"
DISPOSITION_REJECTED_BY_STRUCTURAL_VALIDATION = "rejected_by_structural_validation"
DISPOSITION_REJECTED_BY_RESULT_VALIDATION = "rejected_by_result_validation"
DISPOSITION_REJECTED_BY_SEMANTIC_VERIFIER = "rejected_by_semantic_verifier"
DISPOSITION_SUPERSEDED_BY_FAILURE_ESCALATION = "superseded_by_failure_escalation"
DISPOSITION_SUPERSEDED_BY_SEMANTIC_ESCALATION = "superseded_by_semantic_escalation"
DISPOSITION_EXECUTION_FAILED = "execution_failed"
DISPOSITION_PLANNER_FAILED = "planner_failed"
DISPOSITION_CANNOT_PLAN = "cannot_plan"
DISPOSITION_OPERATIONAL_FAILURE = "operational_failure"
DISPOSITION_PENDING = "pending"

DISPOSITIONS = frozenset(
    {
        DISPOSITION_FINAL,
        DISPOSITION_REJECTED_BY_STRUCTURAL_VALIDATION,
        DISPOSITION_REJECTED_BY_RESULT_VALIDATION,
        DISPOSITION_REJECTED_BY_SEMANTIC_VERIFIER,
        DISPOSITION_SUPERSEDED_BY_FAILURE_ESCALATION,
        DISPOSITION_SUPERSEDED_BY_SEMANTIC_ESCALATION,
        DISPOSITION_EXECUTION_FAILED,
        DISPOSITION_PLANNER_FAILED,
        DISPOSITION_CANNOT_PLAN,
        DISPOSITION_OPERATIONAL_FAILURE,
        DISPOSITION_PENDING,
    }
)

# Escalation triggers (distinguish semantic vs failure)
TRIGGER_SEMANTIC_ESCALATION = "semantic_escalation"
TRIGGER_FAILURE_ESCALATION = "failure_escalation"
TRIGGER_NONE = "none"

# Attempt stage labels (repo-derived)
STAGE_FAST_SUCCESS = "fast_success"
STAGE_FAST_PATH = "fast_path"
STAGE_FAILURE_ESCALATION_SUCCESS = "failure_escalation_success"
STAGE_SEMANTIC_STRONG = "semantic_strong"
STAGE_PLANNER_RETRY_INTERNAL = "planner_retry_internal"  # not a new attempt by default

LINEAGE_SCHEMA_VERSION = 1

_lock = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def plan_fingerprint(plan: IntegrationPlan | dict[str, Any] | None) -> str | None:
    """Deterministic structural plan fingerprint (hash of canonical signature)."""
    if plan is None:
        return None
    try:
        sig = canonical_integration_plan_signature(plan)
        return hashlib.sha256(sig.encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001
        return None


def compact_result_fingerprint(result_fp: dict[str, Any] | None) -> str | None:
    """Privacy-conscious compact hash from existing structural fingerprint dict."""
    if not isinstance(result_fp, dict):
        return None
    try:
        slim = {
            "shape": result_fp.get("shape"),
            "columns": result_fp.get("columns"),
            "content_hash_head50": result_fp.get("content_hash_head50"),
        }
        blob = repr(slim)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
    except Exception:  # noqa: BLE001
        return None


def new_attempt_id(*, request_id: str | None, seq: int) -> str:
    """Unique within a request; independent of semantic content."""
    rid = (request_id or "req").replace(" ", "")[:32]
    return f"{rid}:A{seq}:{uuid.uuid4().hex[:8]}"


def new_verifier_invocation_id() -> str:
    return str(uuid.uuid4())


@dataclass
class AttemptRecord:
    attempt_id: str
    request_id: str | None
    seq: int
    stage: str
    planner_model: str | None = None
    planner_path: str | None = None  # fast | strong | semantic_strong
    plan_fingerprint: str | None = None
    result_fingerprint: str | None = None
    parent_attempt_id: str | None = None
    escalation_trigger: str | None = None  # semantic_escalation | failure_escalation | none
    disposition: str = DISPOSITION_PENDING
    verifier_invocation_ids: list[str] = field(default_factory=list)
    created_at_utc: str = field(default_factory=_utc_now)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "request_id": self.request_id,
            "seq": self.seq,
            "stage": self.stage,
            "planner_model": self.planner_model,
            "planner_path": self.planner_path,
            "plan_fingerprint": self.plan_fingerprint,
            "result_fingerprint": self.result_fingerprint,
            "parent_attempt_id": self.parent_attempt_id,
            "escalation_trigger": self.escalation_trigger,
            "disposition": self.disposition,
            "verifier_invocation_ids": list(self.verifier_invocation_ids),
            "became_final": self.disposition == DISPOSITION_FINAL,
            "created_at_utc": self.created_at_utc,
            "notes": list(self.notes),
        }


@dataclass
class RequestAttemptLineage:
    """In-request attempt tracker. Observational only."""

    request_id: str | None = None
    schema_version: int = LINEAGE_SCHEMA_VERSION
    attempts: list[AttemptRecord] = field(default_factory=list)
    final_attempt_id: str | None = None
    lineage_error: str | None = None

    def _next_seq(self) -> int:
        return len(self.attempts) + 1

    def create_attempt(
        self,
        *,
        stage: str,
        plan: IntegrationPlan | dict[str, Any] | None = None,
        planner_model: str | None = None,
        planner_path: str | None = None,
        parent_attempt_id: str | None = None,
        escalation_trigger: str | None = TRIGGER_NONE,
        result_fingerprint: str | None = None,
        notes: list[str] | None = None,
    ) -> AttemptRecord:
        seq = self._next_seq()
        rec = AttemptRecord(
            attempt_id=new_attempt_id(request_id=self.request_id, seq=seq),
            request_id=self.request_id,
            seq=seq,
            stage=stage,
            planner_model=planner_model,
            planner_path=planner_path,
            plan_fingerprint=plan_fingerprint(plan),
            result_fingerprint=result_fingerprint,
            parent_attempt_id=parent_attempt_id,
            escalation_trigger=escalation_trigger or TRIGGER_NONE,
            notes=list(notes or []),
        )
        self.attempts.append(rec)
        return rec

    def get(self, attempt_id: str) -> AttemptRecord | None:
        for a in self.attempts:
            if a.attempt_id == attempt_id:
                return a
        return None

    def attach_verifier_invocation(
        self, attempt_id: str, verifier_invocation_id: str
    ) -> None:
        att = self.get(attempt_id)
        if att is None:
            return
        if verifier_invocation_id not in att.verifier_invocation_ids:
            att.verifier_invocation_ids.append(verifier_invocation_id)

    def set_disposition(self, attempt_id: str, disposition: str) -> None:
        if disposition not in DISPOSITIONS:
            disposition = DISPOSITION_OPERATIONAL_FAILURE
        att = self.get(attempt_id)
        if att is None:
            return
        # Immutability: do not rewrite core provenance; disposition may advance.
        att.disposition = disposition

    def set_final(self, attempt_id: str) -> None:
        """Mark one attempt as the final Shadow result; others stay non-final."""
        self.final_attempt_id = attempt_id
        for a in self.attempts:
            if a.attempt_id == attempt_id:
                a.disposition = DISPOSITION_FINAL
            elif a.disposition == DISPOSITION_FINAL:
                # Should not happen; keep prior disposition notes
                a.notes.append("final_reassigned_away")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "final_attempt_id": self.final_attempt_id,
            "attempts": [a.to_dict() for a in self.attempts],
            "lineage_error": self.lineage_error,
        }

    def capture_fields_for(self, attempt_id: str) -> dict[str, Any]:
        """Fields to attach to a verifier capture for this attempt."""
        att = self.get(attempt_id)
        if att is None:
            return {
                "attempt_id": None,
                "request_id": self.request_id,
                "plan_fingerprint": None,
                "result_fingerprint": None,
                "planner_model": None,
                "attempt_stage": None,
                "parent_attempt_id": None,
                "escalation_trigger": None,
                "became_final": None,
                "final_attempt_id": self.final_attempt_id,
                "lineage_schema_version": self.schema_version,
            }
        return {
            "attempt_id": att.attempt_id,
            "request_id": self.request_id or att.request_id,
            "plan_fingerprint": att.plan_fingerprint,
            "result_fingerprint": att.result_fingerprint,
            "planner_model": att.planner_model,
            "attempt_stage": att.stage,
            "parent_attempt_id": att.parent_attempt_id,
            "escalation_trigger": att.escalation_trigger,
            # became_final may be unknown until finalization
            "became_final": (
                True
                if att.disposition == DISPOSITION_FINAL
                else (
                    False
                    if att.disposition
                    in {
                        DISPOSITION_REJECTED_BY_SEMANTIC_VERIFIER,
                        DISPOSITION_SUPERSEDED_BY_SEMANTIC_ESCALATION,
                        DISPOSITION_SUPERSEDED_BY_FAILURE_ESCALATION,
                        DISPOSITION_REJECTED_BY_STRUCTURAL_VALIDATION,
                        DISPOSITION_REJECTED_BY_RESULT_VALIDATION,
                        DISPOSITION_EXECUTION_FAILED,
                        DISPOSITION_PLANNER_FAILED,
                        DISPOSITION_CANNOT_PLAN,
                        DISPOSITION_OPERATIONAL_FAILURE,
                    }
                    else None
                )
            ),
            "final_attempt_id": self.final_attempt_id,
            "attempt_disposition": att.disposition,
            "lineage_schema_version": self.schema_version,
        }


def lineage_from_metadata(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    """Read lineage block from pipeline metadata; null-safe for historical records."""
    if not isinstance(meta, dict):
        return None
    block = meta.get("attempt_lineage")
    if not isinstance(block, dict):
        return None
    return block


def safe_lineage_call(fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    """Run lineage helper; swallow errors so telemetry cannot affect semantics."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"lineage_error": f"{type(exc).__name__}: {exc}"}


def evaluate_attribution_regression(
    *,
    verified_attempt_id: str,
    verified_plan_fingerprint: str | None,
    verified_verdict: str,
    final_attempt_id: str,
    final_plan_fingerprint: str | None,
    final_shadow_correct: bool | None = None,
) -> dict[str, Any]:
    """Prevent Phase 39M misread: do not treat earlier FAIL as FF on final plan.

    Evaluation helper only — not production decision logic.
    """
    same_attempt = verified_attempt_id == final_attempt_id
    same_plan = (
        verified_plan_fingerprint is not None
        and verified_plan_fingerprint == final_plan_fingerprint
    )
    invalid_ff_shortcut = (
        (not same_attempt or not same_plan)
        and str(verified_verdict).lower() == "fail"
        and final_shadow_correct is True
    )
    return {
        "verified_attempt_id": verified_attempt_id,
        "final_attempt_id": final_attempt_id,
        "same_attempt": same_attempt,
        "same_plan_fingerprint": same_plan,
        "verified_verdict": verified_verdict,
        "final_shadow_correct": final_shadow_correct,
        "invalid_false_fail_shortcut": invalid_ff_shortcut,
        "rule": (
            "Verifier false-fail requires Verifier-Evaluated Attempt Manual Correct=YES "
            "AND verdict=FAIL; never Final Shadow Correct + earlier attempt FAIL."
        ),
    }
