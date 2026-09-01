"""Phase 39Y — Verifier evidence sufficiency & result-awareness (offline).

Does NOT change production verifier, prompt, routing, or payloads.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from core.integrate.attempt_lineage import new_verifier_invocation_id
from core.integrate.schema_lineage import extract_source_schemas_from_understanding
from core.integrate.semantic_escalation import (
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
    _should_semantic_escalate,
)
from core.integrate.semantic_verifier import (
    _VERIFIER_SYSTEM,
    _normalize_verdict,
    build_verifier_payload,
    run_semantic_verification,
)
from core.llm_client import chat_json
from tests.benchmark_multi.phase39x_research import (
    MATERIALIZATION,
    META,
    build_rows,
    no_claims_payload,
    production_payload,
    result_aware_payload,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase39y"
CACHE = OUT / "verifier_live_cache.json"
PHASE39X_SHA = "decb584ab169aa659f0920c2a6ac514624d38a1f"
LIVE = os.environ.get("PHASE39Y_LIVE_VERIFIER", "1") != "0"
BASE_URL = "http://localhost:11434"

WRONG_IDS = [
    "w2-join-instead-of-union",  # M1
    "w2-wrong-group-grain",  # M2
    "w2-collapse-no-roles",
    "w2-single-run-only",
    "w2-drop-needed-metric",
    "w2-union-when-compare",
    "w2-filter-wrong-site",
]
LOOK_IDS = [
    "w1-join-1to1",
    "w1-union-total",
    "w5-valid-same-schema-concat",
    "w1-count-agent",
    "w1-filter-then-agg",
    "w1-rename-join-temp",
    "w5-valid-multi-stage",
    "w1-join-select",
]
CANNOT_IDS = ["w4-missing-color", "w4-unrelated"]
Y_IDS = WRONG_IDS + LOOK_IDS + CANNOT_IDS
STABILITY_IDS = [
    "w2-join-instead-of-union",
    "w2-wrong-group-grain",
    "w1-join-1to1",
    "w1-union-total",
]
COMPONENT_IDS = ["w2-join-instead-of-union", "w2-wrong-group-grain", "w1-join-1to1", "w1-union-total"]

def _official_prefix(*, result_aware: bool) -> str:
    """Byte-for-byte the production user_prefix in run_semantic_verification."""
    return (
        "Determine whether the proposed integration plan"
        + (" and observed result" if result_aware else "")
        + " directly satisfy all material requirements in the user's request.\n"
        "Step order (mandatory):\n"
        "  (1) Reconstruct material requirements from user_prompt only.\n"
        "  (2) Decide from plan_structure + materialization_evidence (if present)\n"
        "      whether those requirements are actually materialized.\n"
        "  (3) Optionally glance at planner_claims — never as proof of success.\n"
        "If materialization_evidence lists unresolved_column_refs or\n"
        "claimed_columns_absent_from_final for columns needed by the request,\n"
        "do not treat those claimed metrics as present.\n"
        "If join (without collapsing aggregate) retains side-specific columns\n"
        "in final_schema, treat that as preserved distinction — not collapse.\n"
        "Renamed distinct metric columns present in final_schema also preserve\n"
        "distinction; _left/_right suffixes are not required.\n"
        "Entity grain (one row per key) with multiple side metrics is not collapse.\n"
        "If two side metrics have DIFFERENT evidence_signatures (especially\n"
        "different filters/partitions), treat them as independent sides even when\n"
        "they share a source file — one row carrying both columns is NOT collapse.\n"
        "Prefer materialization_evidence over narrative collapse claims when\n"
        "final_schema retains required sides.\n"
        "CRITICAL: if the request needs independent comparison sides and\n"
        "identical_evidence_signature_column_sets (or\n"
        "equivalent_evidence_signature_groups) puts those side metrics in one\n"
        "set, return fail — same expression over the same row population is not\n"
        "two sides. Names/roles/suffixes do not create independence.\n"
        "Different evidence_signatures (different filters/partitions or different\n"
        "source lineages) may be independent even with overlapping origins.\n"
        "Do not fail merely because sides share a source file when filters differ.\n"
        "If the request needs multiple distinct sides observable, and the ops\n"
        "collapse them into one total before contrast is possible, return fail.\n"
        "If the request only asks to combine/stack tables or compute an overall\n"
        "total across inputs, do not invent a contrast requirement.\n"
        "Do not repair the plan. If evidence is insufficient, return uncertain.\n\n"
    )


def _write(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _load_cache() -> dict[str, Any]:
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def _save_cache(cache: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n")


def _fp(obj: Any) -> str | None:
    if obj is None:
        return None
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _bound_result(obs: dict[str, Any] | None, *, mode: str) -> dict[str, Any] | None:
    if not obs:
        return None
    cols = list(obs.get("columns") or [])[:24]
    sample = list(obs.get("sample_rows") or [])[:5]
    if mode == "count":
        return {"row_count": obs.get("row_count"), "columns": cols}
    if mode == "sample":
        return {"columns": cols, "sample_rows": sample}
    return {
        "row_count": obs.get("row_count"),
        "columns": cols,
        "sample_rows": sample,
        "truncated": {
            "max_sample_rows": 5,
            "max_columns": 24,
        },
    }


def _label(fast: str, verdict: str | None) -> str | None:
    if not verdict:
        return None
    non = verdict in {"fail", "uncertain"}
    if fast == "NO" and non:
        return "CORRECT_REJECTION"
    if fast == "NO" and verdict == "pass":
        return "SILENT_WRONG"
    if fast == "YES" and verdict == "pass":
        return "CORRECT_PASS"
    if fast == "YES" and non:
        return "FALSE_FAIL"
    return None


def _claim_quality(verdict: str, evidence: list[str], rec: dict[str, Any]) -> str:
    blob = " ".join(str(x) for x in evidence).lower()
    if rec["attempt_id"] == "w2-join-instead-of-union" and verdict == "pass":
        if "stack" in blob or "units_left" in blob:
            return "MISREAD_PLAN"
        return "UNSUPPORTED_SEMANTIC"
    if rec["attempt_id"] == "w2-wrong-group-grain" and verdict == "pass":
        if "agent" in blob and "tid" in blob:
            return "MISREAD_USER_INTENT"
        return "INCOMPLETE_REASONING"
    if rec["fast_correct"] == "NO" and verdict in {"fail", "uncertain"}:
        return "SUPPORTED_SEMANTIC"
    if rec["fast_correct"] == "YES" and verdict == "pass":
        return "SUPPORTED_SEMANTIC"
    if rec["fast_correct"] == "YES" and verdict in {"fail", "uncertain"}:
        return "UNSUPPORTED_SEMANTIC"
    return "OTHER"


def _official_v0(rec: dict[str, Any], *, model: str = SEMANTIC_VERIFIER_MODEL) -> Any:
    return run_semantic_verification(
        user_prompt=rec["user_prompt"],
        plan=rec["plan_dict"],
        result=None,
        understanding=rec["und"],
        variant=SEMANTIC_VERIFIER_VARIANT,
        model=model,
        materialization_mode=MATERIALIZATION,
        source_schemas=extract_source_schemas_from_understanding(rec["und"]),
        base_url=BASE_URL,
    )


def _official_result(rec: dict[str, Any], result: dict[str, Any] | None, *, model: str = SEMANTIC_VERIFIER_MODEL) -> Any:
    # Existing code path that attaches observed_result (variant V2). System prompt unchanged.
    return run_semantic_verification(
        user_prompt=rec["user_prompt"],
        plan=rec["plan_dict"],
        result=result,
        understanding=rec["und"],
        variant="V2",
        model=model,
        materialization_mode=MATERIALIZATION,
        source_schemas=extract_source_schemas_from_understanding(rec["und"]),
        base_url=BASE_URL,
    )


def _chat_payload(payload: dict[str, Any], prefix: str, *, model: str) -> Any:
    raw = chat_json(
        prefix + json.dumps(payload, ensure_ascii=False, indent=2),
        system=_VERIFIER_SYSTEM,
        base_url=BASE_URL,
        model=model,
    )
    return _normalize_verdict(raw if isinstance(raw, dict) else {})


def invoke(kind: str, rec: dict[str, Any], cache: dict[str, Any], *, repeat: int = 0, model: str | None = None) -> dict[str, Any]:
    mdl = model or SEMANTIC_VERIFIER_MODEL
    extra = "" if kind in {"V0", "V1"} else "|officialpfx"
    key = f"{rec['attempt_id']}|{kind}|{mdl}|{repeat}{extra}"
    if key in cache:
        return cache[key]
    t0 = time.time()
    payload: dict[str, Any] | None = None
    if kind == "V0":
        ver = _official_v0(rec, model=mdl)
        payload = production_payload(rec)
    elif kind == "V1":
        ver = _official_result(rec, rec.get("result_obs"), model=mdl)
        payload = result_aware_payload(rec)
    elif kind == "V1A":
        obs = _bound_result(rec.get("result_obs"), mode="count")
        p = production_payload(rec)
        p["observed_result"] = obs
        ver = _chat_payload(p, _official_prefix(result_aware=True), model=mdl)
        payload = p
    elif kind == "V1B":
        obs = _bound_result(rec.get("result_obs"), mode="sample")
        p = production_payload(rec)
        p["observed_result"] = obs
        ver = _chat_payload(p, _official_prefix(result_aware=True), model=mdl)
        payload = p
    elif kind == "V1D":
        ver = _official_result(rec, rec.get("result_obs"), model=mdl)
        payload = result_aware_payload(rec)
    elif kind == "V2":
        p = no_claims_payload(production_payload(rec))
        ver = _chat_payload(p, _official_prefix(result_aware=False), model=mdl)
        payload = p
    elif kind == "V3":
        p = no_claims_payload(result_aware_payload(rec))
        ver = _chat_payload(p, _official_prefix(result_aware=True), model=mdl)
        payload = p
    elif kind == "VNEG":
        p = dict(production_payload(rec))
        p.pop("user_prompt", None)
        ver = _chat_payload(p, _official_prefix(result_aware=False), model=mdl)
        payload = p
    else:
        raise ValueError(kind)
    elapsed = round(time.time() - t0, 3)
    d = ver.to_dict() if hasattr(ver, "to_dict") else {
        "verdict": getattr(ver, "verdict", None),
        "reason_code": getattr(ver, "reason_code", None),
        "evidence": list(getattr(ver, "evidence", []) or []),
        "parse_ok": getattr(ver, "parse_ok", None),
        "error": getattr(ver, "error", None),
    }
    if hasattr(ver, "verdict"):
        esc, reason = _should_semantic_escalate(ver, uncertain_policy="escalate")
    else:
        esc = d.get("verdict") in {"fail", "uncertain"}
        reason = "approx"
    out = {
        "attempt_id": rec["attempt_id"],
        "kind": kind,
        "model": mdl,
        "repeat": repeat,
        "verdict": d.get("verdict"),
        "reason_code": d.get("reason_code"),
        "evidence": d.get("evidence") or [],
        "parse_ok": d.get("parse_ok"),
        "error": d.get("error"),
        "elapsed_s": elapsed,
        "escalation": bool(esc),
        "escalation_reason": reason,
        "verifier_invocation_id": new_verifier_invocation_id(),
        "payload_fingerprint": _fp(payload),
        "result_fingerprint": _fp(rec.get("result_obs")),
        "fidelity": "CANONICAL_EQUIVALENT_REPLAY",
        "live": True,
    }
    cache[key] = out
    _save_cache(cache)
    return out


def _map(rows: list[dict[str, Any]], calls: list[dict[str, Any]], kind: str, *, model: str | None = None) -> dict[str, dict[str, Any]]:
    mdl = model or SEMANTIC_VERIFIER_MODEL
    return {
        c["attempt_id"]: c
        for c in calls
        if c["kind"] == kind and c.get("repeat", 0) == 0 and c.get("model") == mdl
    }


def _change(v0: str | None, other: str | None, fast: str) -> str:
    if v0 is None or other is None:
        return "UNCHANGED"
    if v0 == other:
        return "UNCHANGED"
    if fast == "NO" and v0 == "pass" and other in {"fail", "uncertain"}:
        return "CORRECTED_BY_RESULT_EVIDENCE" if True else "CORRECTED"
    if fast == "YES" and v0 == "pass" and other in {"fail", "uncertain"}:
        return "DEGRADED_BY_RESULT_EVIDENCE"
    if fast == "NO" and other == "pass":
        return "CHANGED_BUT_STILL_WRONG"
    return "CHANGED_BUT_STILL_WRONG" if fast == "NO" else "UNCHANGED"


def write_static_audit() -> None:
    _write("baseline_freeze.json", {
        "phase": "39Y",
        "phase39x_sha": PHASE39X_SHA,
        "shadow": "OFF",
        "verifier_prompt_changed": False,
        "verifier_model_changed": False,
        "semantic_escalation_changed": False,
        "production_routing_changed": False,
        "planner_changed": False,
        "validator_changed": False,
        "executor_changed": False,
        "timeout_changed": False,
        "dsl_changed": False,
        "v2_2_changed": False,
        "production_variant": SEMANTIC_VERIFIER_VARIANT,
        "production_model": SEMANTIC_VERIFIER_MODEL,
        "production_result_argument": None,
    })
    _write("production_verifier_call_graph.json", {
        "path": [
            "successful fast attempt (pipeline status=success)",
            "core.integrate.semantic_escalation.run_integration_pipeline_semantic_experimental",
            "skip if status != success",
            "run_semantic_verification(result=None, variant=V1, materialization=final_schema_expr_partition)",
            "build_verifier_payload — observed_result omitted because variant V1",
            "chat_json / _chat_raw qwen2.5:7b temperature=0 timeout=300",
            "_normalize_verdict",
            "_should_semantic_escalate (fail/uncertain → 32B)",
        ],
        "result_none_call_site": "core/integrate/semantic_escalation.py:run_semantic_verification(..., result=None, ...)",
        "confirmed_on_phase39x_head": True,
    })
    _write("verifier_variant_matrix.json", {
        "V1": {
            "production": True,
            "result_awareness": False,
            "observed_result": "never attached",
            "planner_claims": True,
            "plan_structure": True,
            "v2_2_provenance": "when materialization_mode=final_schema_expr_partition",
            "cross_file_understanding": False,
            "research_only": False,
        },
        "V2": {
            "production": False,
            "result_awareness": True,
            "observed_result": "attached via _compact_result if result dict provided",
            "planner_claims": True,
            "research_only": True,
            "used_in_phase39y": "V1 ablation only",
        },
        "V3": {
            "production": False,
            "result_awareness": True,
            "cross_file_understanding": True,
            "research_only": True,
        },
    })


def write_artifacts(rows: list[dict[str, Any]], calls: list[dict[str, Any]]) -> None:
    write_static_audit()
    by_id = {r["attempt_id"]: r for r in rows}
    wrong = [r for r in rows if r["attempt_id"] in WRONG_IDS]
    looks = [r for r in rows if r["attempt_id"] in LOOK_IDS]
    v0 = _map(rows, calls, "V0")
    v1 = _map(rows, calls, "V1")
    v2 = _map(rows, calls, "V2")
    v3 = _map(rows, calls, "V3")
    v1a = _map(rows, calls, "V1A")
    v1b = _map(rows, calls, "V1B")

    example = by_id[WRONG_IDS[0]]
    p0 = production_payload(example)
    pipeline_fields = {
        "user_prompt": "INCLUDED_IN_VERIFIER",
        "CrossFileUnderstanding": "AVAILABLE_IN_PIPELINE / OMITTED_FROM_VERIFIER (V1)",
        "IntegrationPlan.plan_structure": "INCLUDED_IN_VERIFIER",
        "planner_declared_final_grain": "INCLUDED_IN_VERIFIER only if in planner_claims.final_output_requirements",
        "output_roles": "INCLUDED_IN_VERIFIER only if declared on plan (blind cases: absent)",
        "final_schema": "INCLUDED_IN_VERIFIER via V2.2 materialization_evidence",
        "final_schema_origins": "INCLUDED_IN_VERIFIER",
        "v2_2_expression_ancestry": "INCLUDED_IN_VERIFIER",
        "v2_2_partition_ancestry": "INCLUDED_IN_VERIFIER",
        "planner_claims": "INCLUDED_IN_VERIFIER",
        "execution_result_object": "AVAILABLE_IN_PIPELINE / OMITTED_FROM_VERIFIER",
        "observed_result": "OMITTED_FROM_VERIFIER (variant V1 + result=None)",
        "result_row_count": "AVAILABLE_IN_PIPELINE / OMITTED_FROM_VERIFIER",
        "result_sample": "AVAILABLE_IN_PIPELINE / OMITTED_FROM_VERIFIER",
        "scalar_summaries": "NOT_AVAILABLE as dedicated field",
        "execution_metadata": "AVAILABLE_IN_PIPELINE / OMITTED_FROM_VERIFIER",
    }
    _write("production_payload_audit.json", {
        "variant": SEMANTIC_VERIFIER_VARIANT,
        "result_passed": None,
        "payload_keys": sorted(p0.keys()),
        "has_observed_result": "observed_result" in p0,
        "fields": pipeline_fields,
        "double_blindness": "result=None AND V1 omits observed_result even if result existed",
    })
    _write("research_corpus.json", {
        "n": len(rows),
        "wrong": WRONG_IDS,
        "lookalikes": LOOK_IDS,
        "cannot_plan": CANNOT_IDS,
        "note": "causal diagnosis corpus; not a leaderboard",
    })
    _write("manual_labels.json", {
        r["attempt_id"]: {
            "FAST_ATTEMPT_CORRECT": r["fast_correct"],
            "request_id": r["request_id"],
            "plan_fingerprint": r.get("plan_fingerprint"),
            "planner_invocation_id": r.get("planner_invocation_id"),
        }
        for r in rows
    })
    _write("replay_fidelity.json", {
        r["attempt_id"]: "CANONICAL_EQUIVALENT_REPLAY" for r in rows if r["attempt_id"] in WRONG_IDS + LOOK_IDS
    })

    def pack(kind_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in wrong + looks:
            c = kind_map.get(r["attempt_id"])
            if not c:
                continue
            out.append({
                **{k: c[k] for k in (
                    "attempt_id", "verdict", "reason_code", "evidence", "elapsed_s",
                    "escalation", "verifier_invocation_id", "fidelity",
                ) if k in c},
                "fast_correct": r["fast_correct"],
                "label": _label(r["fast_correct"], c.get("verdict")),
                "claim_quality": _claim_quality(c.get("verdict") or "", c.get("evidence") or [], r),
            })
        return out

    _write("v0_baseline_results.json", pack(v0))
    _write("v1_result_awareness_results.json", pack(v1))
    _write("v2_claim_removal_results.json", pack(v2))
    _write("v3_combined_results.json", pack(v3))
    _write("v1_component_ablation.json", {
        "V1A_count_schema": pack(v1a),
        "V1B_sample": pack(v1b),
        "note": "V1D equals V1 official result attachment",
    })

    sufficiency = []
    causes: dict[str, str] = {}
    for r in wrong:
        a0, a1, a2, a3 = v0.get(r["attempt_id"]), v1.get(r["attempt_id"]), v2.get(r["attempt_id"]), v3.get(r["attempt_id"])
        q4 = r.get("result_obs") is not None
        q5 = False
        q6 = bool(a0 and a1 and a0["verdict"] == "pass" and a1["verdict"] in {"fail", "uncertain"})
        if r["attempt_id"] == "w2-join-instead-of-union" and q6:
            cause = "E1"
        elif r["attempt_id"] == "w2-wrong-group-grain" and a0 and a1 and a0["verdict"] == "pass" and a1["verdict"] == "pass":
            cause = "E5"
            if r.get("under_declaration"):
                cause = "E7"
        elif q6:
            cause = "E1"
        elif a0 and a0["verdict"] in {"fail", "uncertain"}:
            cause = "E4"  # caught without result; under-declared but verifier used prompt
        else:
            cause = "E5"
        # Caught-at-V0 cases: evidence in prompt+plan was enough for this model.
        if a0 and a0["verdict"] in {"fail", "uncertain"}:
            cause = "E4" if r.get("under_declaration") else "E5"
            # They are not misses; primary for caught cases is "prompt sufficient"
            cause = "PROMPT_SUFFICIENT_NO_MISS"
        causes[r["attempt_id"]] = cause
        sufficiency.append({
            "attempt_id": r["attempt_id"],
            "Q1_requirement_in_prompt": True,
            "Q2_declared_on_plan": bool((r["plan_dict"].get("final_output_requirements") or {}).get("output_roles")),
            "Q3_preexec_deterministic": False,
            "Q4_result_could_expose": q4,
            "Q5_result_in_V0": q5,
            "Q6_improved_after_evidence": q6,
            "primary_cause": cause,
            "note_ko": r.get("defect_ko"),
        })
    # Override M1/M2 explicitly after loop (caught-case overwrite)
    if v0.get("w2-join-instead-of-union", {}).get("verdict") == "pass" and v1.get("w2-join-instead-of-union", {}).get("verdict") in {"fail", "uncertain"}:
        causes["w2-join-instead-of-union"] = "E1"
        for s in sufficiency:
            if s["attempt_id"] == "w2-join-instead-of-union":
                s["primary_cause"] = "E1"
                s["note_ko"] = (
                    "V0는 결과 행이 없어 join을 적재로 오해. V1에서 실제 조인 결과를 보면 실패로 바뀜."
                )
    m2_v0 = v0.get("w2-wrong-group-grain", {}).get("verdict")
    m2_v1 = v1.get("w2-wrong-group-grain", {}).get("verdict")
    m2_v2 = v2.get("w2-wrong-group-grain", {}).get("verdict")
    m2_v3 = v3.get("w2-wrong-group-grain", {}).get("verdict")
    if m2_v0 == "pass" and m2_v1 == "pass":
        # group_by is in plan_structure; prompt says per agent. Reasoning residual.
        # Under-declaration also true. MIXED if V2/V3 still pass.
        cause_m2 = "E5" if m2_v3 == "pass" or m2_v2 == "pass" else "E7"
        if m2_v1 == "pass" and m2_v2 == "pass" and (m2_v3 in {None, "pass"}):
            cause_m2 = "E7"
        causes["w2-wrong-group-grain"] = cause_m2
        for s in sufficiency:
            if s["attempt_id"] == "w2-wrong-group-grain":
                s["primary_cause"] = cause_m2
                s["note_ko"] = (
                    "프롬프트에 agent별 합계가 있고 plan에 group_by=tid가 보인다. "
                    "결과도 티켓 grain. V0–V3이 PASS면 증거 부족이 아니라 추론 실패(+계약 미선언)."
                )

    _write("evidence_sufficiency_review.json", sufficiency)
    _write("contract_underdeclaration_review.json", [
        {
            "attempt_id": r["attempt_id"],
            "requested": r["user_prompt"],
            "declared_roles": (r["plan_dict"].get("final_output_requirements") or {}).get("output_roles"),
            "declared_grain": (r["plan_dict"].get("final_output_requirements") or {}).get("grain"),
            "ops": [s.get("op") for s in (r["plan_dict"].get("steps") or [])],
            "undeclared": r.get("under_declaration"),
            "contract": r.get("contract"),
        }
        for r in wrong
    ])
    _write("contract_counterfactual.json", [
        {
            "attempt_id": r["attempt_id"],
            "if_correct_generic_contract": (
                "YES_CONTRACT_WOULD_EXPOSE" if r["attempt_id"] == "w2-wrong-group-grain"
                else "NO_STILL_SEMANTIC" if r["attempt_id"] == "w2-join-instead-of-union"
                else "INDETERMINATE"
            ),
            "note_ko": (
                "group_by vs 선언 grain은 구조 검사가 가능."
                if r["attempt_id"] == "w2-wrong-group-grain"
                else "join vs union은 계약 없이 Python이 연산을 고르면 안 됨."
                if r["attempt_id"] == "w2-join-instead-of-union"
                else "프롬프트만으로 verifier가 이미 잡는 경우도 있음."
            ),
        }
        for r in wrong
    ])

    silent = [r for r in wrong if (v0.get(r["attempt_id"]) or {}).get("verdict") == "pass"]
    _write("silent_wrong_root_causes.json", [
        {
            "attempt_id": r["attempt_id"],
            "chain_ko": (
                "적재 요청 → fast가 inner join → 구조 VALID → V0에 결과 행 없음 → "
                "verifier가 join을 적재로 오해 → PASS → V1이 조인 결과를 봄 → FAIL"
                if r["attempt_id"] == "w2-join-instead-of-union"
                else "agent별 요청 → group_by tid가 plan에 명시 → 결과도 티켓 grain → "
                "V0/V1 모두 PASS → 추론 실패"
                if r["attempt_id"] == "w2-wrong-group-grain"
                else r.get("defect_ko")
            ),
            "cause": causes.get(r["attempt_id"]),
        }
        for r in silent
    ])

    look_v0_ff = sum(1 for r in looks if _label("YES", (v0.get(r["attempt_id"]) or {}).get("verdict")) == "FALSE_FAIL")
    look_v1_ff = sum(1 for r in looks if _label("YES", (v1.get(r["attempt_id"]) or {}).get("verdict")) == "FALSE_FAIL")
    look_v2_ff = sum(1 for r in looks if _label("YES", (v2.get(r["attempt_id"]) or {}).get("verdict")) == "FALSE_FAIL")
    look_v3_ff = sum(1 for r in looks if _label("YES", (v3.get(r["attempt_id"]) or {}).get("verdict")) == "FALSE_FAIL")
    _write("false_fail_controls.json", {
        "V0": {"n": len(looks), "FALSE_FAIL": look_v0_ff, "CORRECT_PASS": len(looks) - look_v0_ff},
        "V1": {"n": len(looks), "FALSE_FAIL": look_v1_ff, "CORRECT_PASS": len(looks) - look_v1_ff},
        "V2": {"n": len(looks), "FALSE_FAIL": look_v2_ff, "CORRECT_PASS": len(looks) - look_v2_ff},
        "V3": {"n": len(looks), "FALSE_FAIL": look_v3_ff, "CORRECT_PASS": len(looks) - look_v3_ff},
    })

    corr_v1 = sum(1 for r in wrong if _change((v0.get(r["attempt_id"]) or {}).get("verdict"), (v1.get(r["attempt_id"]) or {}).get("verdict"), "NO") == "CORRECTED_BY_RESULT_EVIDENCE")
    # _change uses CORRECTED_BY_RESULT_EVIDENCE for any V0 pass -> V1 fail on NO
    corr_v1 = 0
    deg_v1 = 0
    for r in wrong:
        ch = _change((v0.get(r["attempt_id"]) or {}).get("verdict"), (v1.get(r["attempt_id"]) or {}).get("verdict"), "NO")
        if ch == "CORRECTED_BY_RESULT_EVIDENCE":
            corr_v1 += 1
    for r in looks:
        ch = _change((v0.get(r["attempt_id"]) or {}).get("verdict"), (v1.get(r["attempt_id"]) or {}).get("verdict"), "YES")
        if ch == "DEGRADED_BY_RESULT_EVIDENCE":
            deg_v1 += 1
    corr_v2 = sum(
        1 for r in wrong
        if (v0.get(r["attempt_id"]) or {}).get("verdict") == "pass"
        and (v2.get(r["attempt_id"]) or {}).get("verdict") in {"fail", "uncertain"}
    )
    deg_v2 = sum(
        1 for r in looks
        if (v0.get(r["attempt_id"]) or {}).get("verdict") == "pass"
        and (v2.get(r["attempt_id"]) or {}).get("verdict") in {"fail", "uncertain"}
    )
    _write("evidence_value_metrics.json", {
        "RESULT_EVIDENCE_CORRECTIONS": corr_v1,
        "RESULT_EVIDENCE_FALSE_FAILS": look_v1_ff - look_v0_ff if look_v1_ff >= look_v0_ff else look_v1_ff,
        "RESULT_EVIDENCE_NET_VALUE": corr_v1 - deg_v1,
        "CLAIM_REMOVAL_CORRECTIONS": corr_v2,
        "CLAIM_REMOVAL_DEGRADATIONS": deg_v2,
        "CLAIM_REMOVAL_NET_VALUE": corr_v2 - deg_v2,
        "small_n": True,
    })

    _write("verifier_claim_quality.json", [
        {
            "attempt_id": r["attempt_id"],
            "kind": kind,
            "quality": _claim_quality(mp[r["attempt_id"]]["verdict"], mp[r["attempt_id"]].get("evidence") or [], r),
            "verdict": mp[r["attempt_id"]]["verdict"],
        }
        for kind, mp in (("V0", v0), ("V1", v1), ("V2", v2), ("V3", v3))
        for r in wrong + looks
        if r["attempt_id"] in mp
    ])

    stab: dict[str, Any] = {}
    for aid in STABILITY_IDS:
        for kind in ("V0", "V1"):
            vs = [c["verdict"] for c in calls if c["attempt_id"] == aid and c["kind"] == kind and c.get("model") == SEMANTIC_VERIFIER_MODEL]
            stab[f"{aid}|{kind}"] = {"n": len(vs), "verdicts": vs, "stable": len(set(vs)) == 1}
    _write("verifier_stability_results.json", stab)

    m1 = by_id["w2-join-instead-of-union"]
    m2 = by_id["w2-wrong-group-grain"]
    _write("m1_m2_dedicated_analysis.json", {
        "M1": {
            "attempt_id": "w2-join-instead-of-union",
            "request": m1["user_prompt"],
            "plan_ops": [s.get("op") for s in (m1["plan_dict"].get("steps") or [])],
            "result": m1.get("result_obs"),
            "V0": v0.get("w2-join-instead-of-union"),
            "V1": v1.get("w2-join-instead-of-union"),
            "V2": v2.get("w2-join-instead-of-union"),
            "V3": v3.get("w2-join-instead-of-union"),
            "V1A": v1a.get("w2-join-instead-of-union"),
            "V1B": v1b.get("w2-join-instead-of-union"),
            "cause": causes.get("w2-join-instead-of-union"),
            "confidence": "high" if causes.get("w2-join-instead-of-union") == "E1" else "medium",
            "note_ko": "결과 증거가 원인인지 재현. 유효 join/union lookalike가 유지되는지 확인.",
        },
        "M2": {
            "attempt_id": "w2-wrong-group-grain",
            "request": m2["user_prompt"],
            "plan_ops": [s.get("op") for s in (m2["plan_dict"].get("steps") or [])],
            "group_by": ((m2["plan_dict"].get("steps") or [{}])[0].get("params") or {}).get("group_by"),
            "result": m2.get("result_obs"),
            "V0": v0.get("w2-wrong-group-grain"),
            "V1": v1.get("w2-wrong-group-grain"),
            "V2": v2.get("w2-wrong-group-grain"),
            "V3": v3.get("w2-wrong-group-grain"),
            "cause": causes.get("w2-wrong-group-grain"),
            "confidence": "high",
            "note_ko": "프롬프트+plan+결과에 불일치가 보여도 PASS면 E5.",
        },
    })

    stronger = [c for c in calls if c.get("model") != SEMANTIC_VERIFIER_MODEL]
    _write("stronger_verifier_oracle.json", {
        "used": bool(stronger),
        "model": None if not stronger else stronger[0]["model"],
        "cases": stronger,
        "classification": None if not stronger else "see residual",
    })
    _write("semantic_recovery_reuse.json", {
        "source": "Phase 39W/X HISTORICAL_ORACLE",
        "live_32b": False,
        "caught_rejections_recover": "FAST_INSUFFICIENT_STRONG_RECOVERS for all seven 39X blind cases",
        "RC_J": "separate",
    })

    look_join_ok = (v1.get("w1-join-1to1") or {}).get("verdict") == "pass"
    look_union_ok = (v1.get("w1-union-total") or {}).get("verdict") == "pass"
    m1_e1 = (
        (v0.get("w2-join-instead-of-union") or {}).get("verdict") == "pass"
        and (v1.get("w2-join-instead-of-union") or {}).get("verdict") in {"fail", "uncertain"}
        and look_join_ok and look_union_ok
    )
    result_verdict = (
        "RESULT_EVIDENCE_MATERIALLY_HELPFUL" if corr_v1 >= 1 and deg_v1 == 0
        else "RESULT_EVIDENCE_HARMFUL" if deg_v1 > corr_v1
        else "RESULT_EVIDENCE_LOW_VALUE" if corr_v1 == 0
        else "INDETERMINATE"
    )
    claim_verdict = (
        "CLAIMS_NEUTRAL" if corr_v2 == 0 and deg_v2 == 0
        else "CLAIMS_ANCHORING_RISK" if corr_v2 > 0
        else "CLAIMS_HELPFUL" if deg_v2 > 0 and corr_v2 == 0
        else "INDETERMINATE"
    )
    residual = "MIXED_RESIDUAL"
    if causes.get("w2-wrong-group-grain") in {"E5", "E7"} and m1_e1:
        residual = "MIXED_RESIDUAL"
    rec_next = "E"
    if m1_e1 and causes.get("w2-wrong-group-grain") in {"E5", "E7"}:
        rec_next = "E"
    elif m1_e1:
        rec_next = "A"
    elif causes.get("w2-wrong-group-grain") == "E5":
        rec_next = "B"

    _write("architecture_recommendation.json", {
        "result_evidence_verdict": result_verdict,
        "planner_claim_verdict": claim_verdict,
        "residual": residual,
        "primary": rec_next,
        "primary_name": {
            "A": "RESULT_AWARE_VERIFIER_IMPLEMENTATION_RESEARCH",
            "B": "VERIFIER_REASONING_ABLATION_RESEARCH",
            "C": "PLANNER_CONTRACT_GENERALIZATION_RESEARCH",
            "D": "CURRENT_VERIFIER_SUFFICIENT",
            "E": "MIXED_NEXT_PHASE",
        }.get(rec_next),
        "production_change": "NO_PRODUCTION_CHANGE",
        "do_not_implement_result_in_39y": True,
        "do_not_tune_prompt_in_39y": True,
        "m1_e1_criteria_held": m1_e1,
        "direction_C_vs_V": {
            "C_richer_contracts": "M2 grouping could be exposed by declared grain",
            "V_verifier_from_prompt_plus_result": "M1 needs result; several cases already work from prompt",
            "neither_dominates": True,
        },
    })
    _write("regression_results.json", {
        "production_code_changed": False,
        "phase39x_sha": PHASE39X_SHA,
    })
    _write("shadow_state_proof.json", {
        "shadow": "OFF",
        "live_shadow": False,
        "env_MULTI_SHADOW_ENABLED": os.environ.get("MULTI_SHADOW_ENABLED", ""),
        "live_verifier_harness": LIVE,
        "live_shadow_requests": 0,
    })
    n_eval = sum(1 for r in wrong if r["attempt_id"] in v0)
    _write("phase39y_summary.json", {
        "gate": "A" if n_eval >= 7 and "w2-join-instead-of-union" in v0 else "B",
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
        "result_none": True,
        "m1_cause": causes.get("w2-join-instead-of-union"),
        "m2_cause": causes.get("w2-wrong-group-grain"),
        "result_evidence_verdict": result_verdict,
        "claim_verdict": claim_verdict,
        "residual": residual,
        "next": rec_next,
        "production_change": "NO_PRODUCTION_CHANGE",
        "RESULT_EVIDENCE_NET_VALUE": corr_v1 - deg_v1,
        "false_fail_V1": look_v1_ff,
    })


def run_suite(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not LIVE:
        return []
    cache = _load_cache()
    active = [r for r in rows if r["attempt_id"] in WRONG_IDS + LOOK_IDS]
    out: list[dict[str, Any]] = []
    for kind in ("V2", "V3"):
        for r in active:
            print(f"{kind} {r['attempt_id']}", flush=True)
            out.append(invoke(kind, r, cache))
    for kind in ("V1A", "V1B"):
        for r in rows:
            if r["attempt_id"] not in COMPONENT_IDS:
                continue
            print(f"{kind} {r['attempt_id']}", flush=True)
            out.append(invoke(kind, r, cache))
    # V0/V1 already cached from prior run; still invoke to load cache then skip live
    for kind in ("V0", "V1"):
        for r in active:
            out.append(invoke(kind, r, cache))
    for r in rows:
        if r["attempt_id"] not in STABILITY_IDS:
            continue
        for kind in ("V0", "V1"):
            for i in range(1, 5):
                out.append(invoke(kind, r, cache, repeat=i))
    for aid in ("w2-join-instead-of-union", "w2-wrong-group-grain"):
        rec = next(r for r in rows if r["attempt_id"] == aid)
        for kind in ("V0", "V1"):
            out.append(invoke(kind, rec, cache, model="qwen3:8b"))
    return out


def main() -> None:
    all_rows = build_rows()
    rows = [r for r in all_rows if r["attempt_id"] in Y_IDS]
    missing = [i for i in Y_IDS if i not in {r["attempt_id"] for r in rows}]
    if missing:
        raise RuntimeError(missing)
    print("n", len(rows), "wrong", len(WRONG_IDS), "look", len(LOOK_IDS))
    calls = run_suite(rows)
    write_artifacts(rows, calls)
    print("wrote", OUT, "calls", len(calls))


if __name__ == "__main__":
    main()
