"""Request-identity isolation helpers (provenance only, not semantics).

Association is by stable request_id / attempt_id. Never by completion order,
list position, or a process-global "current/last" context.
"""

from __future__ import annotations

from typing import Any


def bind_records_by_request_id(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Map request_id → last record written for that identity.

    Later writes for the same request replace earlier ones. A later
    *different* request never overwrites an earlier request's slot.
    """
    out: dict[str, dict[str, Any]] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        rid = rec.get("request_id")
        if isinstance(rid, str) and rid:
            out[rid] = rec
    return out


def select_record_for_request(
    records: list[dict[str, Any]],
    request_id: str,
) -> dict[str, Any] | None:
    if not request_id:
        return None
    matches = [
        rec
        for rec in records
        if isinstance(rec, dict) and rec.get("request_id") == request_id
    ]
    return matches[-1] if matches else None


def lineage_integrity_report(
    *,
    request_id: str | None,
    lineage: dict[str, Any] | None,
    verified_attempt_id: str | None = None,
    final_attempt_id: str | None = None,
) -> dict[str, Any]:
    """Deterministic provenance checks. Does not repair mismatches."""
    violations: list[dict[str, Any]] = []
    lin = lineage if isinstance(lineage, dict) else {}
    lin_rid = lin.get("request_id")
    if request_id and lin_rid and lin_rid != request_id:
        violations.append(
            {
                "code": "lineage_request_id_mismatch",
                "request_id": request_id,
                "lineage_request_id": lin_rid,
            }
        )
    attempts = lin.get("attempts") or []
    by_id: dict[str, dict[str, Any]] = {}
    for att in attempts:
        if not isinstance(att, dict):
            continue
        aid = att.get("attempt_id")
        if isinstance(aid, str):
            by_id[aid] = att
        att_rid = att.get("request_id")
        expected = request_id or lin_rid
        if expected and att_rid and att_rid != expected:
            violations.append(
                {
                    "code": "attempt_request_id_mismatch",
                    "request_id": expected,
                    "attempt_id": aid,
                    "attempt_request_id": att_rid,
                }
            )
        parent = att.get("parent_attempt_id")
        if parent:
            parent_rec = by_id.get(str(parent))
            if parent_rec is None:
                violations.append(
                    {
                        "code": "parent_attempt_missing",
                        "attempt_id": aid,
                        "parent_attempt_id": parent,
                    }
                )
            else:
                p_rid = parent_rec.get("request_id")
                if att_rid and p_rid and att_rid != p_rid:
                    violations.append(
                        {
                            "code": "parent_child_request_mismatch",
                            "attempt_id": aid,
                            "parent_attempt_id": parent,
                            "attempt_request_id": att_rid,
                            "parent_request_id": p_rid,
                        }
                    )
    fid = final_attempt_id or lin.get("final_attempt_id")
    if fid:
        final = by_id.get(str(fid))
        if final is not None:
            f_rid = final.get("request_id")
            expected = request_id or lin_rid
            if expected and f_rid and f_rid != expected:
                violations.append(
                    {
                        "code": "final_attempt_request_mismatch",
                        "request_id": expected,
                        "final_attempt_id": fid,
                        "final_request_id": f_rid,
                    }
                )
    vid = verified_attempt_id or lin.get("verified_attempt_id")
    if vid:
        verified = by_id.get(str(vid))
        if verified is not None:
            v_rid = verified.get("request_id")
            expected = request_id or lin_rid
            if expected and v_rid and v_rid != expected:
                violations.append(
                    {
                        "code": "verified_attempt_request_mismatch",
                        "request_id": expected,
                        "verified_attempt_id": vid,
                        "verified_request_id": v_rid,
                    }
                )
        elif request_id and not str(vid).startswith(str(request_id)):
            violations.append(
                {
                    "code": "verified_attempt_id_foreign_prefix",
                    "request_id": request_id,
                    "verified_attempt_id": vid,
                }
            )
    return {
        "ok": not violations,
        "violations": violations,
        "request_id": request_id,
    }


def capture_integrity_report(
    *,
    capture: dict[str, Any] | None,
    request_id: str | None,
    attempt_id: str | None,
    plan_fingerprint: str | None = None,
    result_fingerprint: str | None = None,
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    cap = capture if isinstance(capture, dict) else {}
    if request_id and cap.get("request_id") and cap.get("request_id") != request_id:
        violations.append(
            {
                "code": "capture_request_id_mismatch",
                "request_id": request_id,
                "capture_request_id": cap.get("request_id"),
            }
        )
    if attempt_id and cap.get("attempt_id") and cap.get("attempt_id") != attempt_id:
        violations.append(
            {
                "code": "capture_attempt_id_mismatch",
                "attempt_id": attempt_id,
                "capture_attempt_id": cap.get("attempt_id"),
            }
        )
    if (
        plan_fingerprint
        and cap.get("plan_fingerprint")
        and cap.get("plan_fingerprint") != plan_fingerprint
    ):
        violations.append(
            {
                "code": "capture_plan_fingerprint_mismatch",
                "plan_fingerprint": plan_fingerprint,
                "capture_plan_fingerprint": cap.get("plan_fingerprint"),
            }
        )
    if (
        result_fingerprint
        and cap.get("result_fingerprint")
        and cap.get("result_fingerprint") != result_fingerprint
    ):
        violations.append(
            {
                "code": "capture_result_fingerprint_mismatch",
                "result_fingerprint": result_fingerprint,
                "capture_result_fingerprint": cap.get("result_fingerprint"),
            }
        )
    return {"ok": not violations, "violations": violations}


def p39p_contamination_shape(
    *,
    request_b_id: str,
    request_a_attempt_id: str,
    request_b_verified_attempt_id: str | None,
    request_a_final_attempt_id: str | None,
    request_b_attempt_id: str | None,
) -> dict[str, Any]:
    """Phase 39P Gate C shape: B's verified attempt is A's, or A's final is B's."""
    flags: list[str] = []
    if (
        request_b_verified_attempt_id
        and request_a_attempt_id
        and request_b_verified_attempt_id == request_a_attempt_id
    ):
        flags.append("B_verified_attempt_is_A")
    if (
        request_b_verified_attempt_id
        and request_b_id
        and not str(request_b_verified_attempt_id).startswith(str(request_b_id))
    ):
        flags.append("B_verified_attempt_foreign_prefix")
    if (
        request_a_final_attempt_id
        and request_b_attempt_id
        and request_a_final_attempt_id == request_b_attempt_id
    ):
        flags.append("A_final_attempt_is_B")
    return {
        "contaminated": bool(flags),
        "flags": flags,
        "request_b_id": request_b_id,
        "request_a_attempt_id": request_a_attempt_id,
        "request_b_verified_attempt_id": request_b_verified_attempt_id,
        "request_a_final_attempt_id": request_a_final_attempt_id,
        "request_b_attempt_id": request_b_attempt_id,
    }
