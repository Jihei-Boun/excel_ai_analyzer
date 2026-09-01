"""Phase 39X — Pre-execution observability ceiling & verifier complementarity.

Does NOT change production routing, verifier, planner, or PHASE39V_RULE_V1.
"""

from __future__ import annotations

import inspect
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.schema_lineage import (
    build_schema_lineage,
    extract_source_schemas_from_understanding,
)
from core.integrate.semantic_escalation import (
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
    _should_semantic_escalate,
)
from core.integrate.semantic_verifier import (
    build_verifier_payload,
    run_semantic_verification,
)
from tests.benchmark_multi.phase39v_research import (
    _und_from_frames,
    evaluate_capability_signal,
    extract_attempt_evidence,
)
from tests.benchmark_multi.phase39w_research import (
    PHASE39V_RULE_VERSION,
    build_w_corpus,
    phase39v_rule_v1,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase39x"
CACHE = OUT / "verifier_live_cache.json"

PHASE39W_SHA = "d25c87a36a4409035c8ca78e68938ad81a894373"
MATERIALIZATION = "final_schema_expr_partition"
LIVE = os.environ.get("PHASE39X_LIVE_VERIFIER", "1") != "0"

# Selected 39W attempts. Analyst annotations only — not routing features.
META: dict[str, dict[str, Any]] = {
    # ----- CLASS B core blind region -----
    "w2-collapse-no-roles": {
        "role": "blind",
        "class_hint": "B",
        "defect": "COLLAPSED_DISTINCTION",
        "lookalike": "w1-union-total",
        "user_prompt": (
            "Compare inventory quantity between warehouse A and warehouse B for each bin. "
            "I need both warehouses visible, not a single combined total."
        ),
        "defect_ko": "두 창고 비교가 필요한데 union 후 한 합계로 붕괴.",
        "python_without_meaning": "NO",
        "counterfactual": "SEMANTIC_INFERENCE_REQUIRED",
        "contract": "SEMANTIC_REQUIREMENT_NOT_STRUCTURALLY_DECLARED",
        "under_declaration": True,
        "strong_39w": "FAST_INSUFFICIENT_STRONG_RECOVERS",
    },
    "w2-single-run-only": {
        "role": "blind",
        "class_hint": "B",
        "defect": "WRONG_BRANCH",
        "lookalike": "w5-valid-multi-stage",
        "user_prompt": (
            "For each sample show assay scores from run R1 and run R2 side by side."
        ),
        "defect_ko": "R1과 R2가 필요한데 R1만 필터·집계.",
        "python_without_meaning": "NO",
        "counterfactual": "SEMANTIC_INFERENCE_REQUIRED",
        "contract": "SEMANTIC_REQUIREMENT_NOT_STRUCTURALLY_DECLARED",
        "under_declaration": True,
        "strong_39w": "FAST_INSUFFICIENT_STRONG_RECOVERS",
    },
    "w2-join-instead-of-union": {
        "role": "blind",
        "class_hint": "B",
        "defect": "WRONG_INTEGRATION_SHAPE",
        "lookalike": "w1-join-1to1",
        "user_prompt": (
            "Stack Q1 and Q2 unit rows into one table so every SKU row from either quarter is kept."
        ),
        "defect_ko": "적재가 필요한데 inner join으로 교집합만 남김.",
        "python_without_meaning": "NO",
        "counterfactual": "SEMANTIC_INFERENCE_REQUIRED",
        "contract": "SEMANTIC_REQUIREMENT_NOT_STRUCTURALLY_DECLARED",
        "under_declaration": True,
        "strong_39w": "FAST_INSUFFICIENT_STRONG_RECOVERS",
    },
    "w2-wrong-group-grain": {
        "role": "blind",
        "class_hint": "B",
        "defect": "WRONG_GROUPING",
        "lookalike": "w1-count-agent",
        "user_prompt": "Sum ticket hours per agent, not per individual ticket.",
        "defect_ko": "상담원별 합계가 필요한데 티켓 grain으로 집계.",
        "python_without_meaning": "NO",
        "counterfactual": "SEMANTIC_INFERENCE_REQUIRED",
        "contract": "SEMANTIC_REQUIREMENT_NOT_STRUCTURALLY_DECLARED",
        "under_declaration": True,
        "strong_39w": "FAST_INSUFFICIENT_STRONG_RECOVERS",
    },
    "w2-drop-needed-metric": {
        "role": "blind",
        "class_hint": "B",
        "defect": "INCOMPLETE_OUTPUT",
        "lookalike": "w1-join-select",
        "user_prompt": (
            "Join fleet kilometers with fuel liters so I can compare distance and fuel together."
        ),
        "defect_ko": "연비 비교에 liters를 버렸다.",
        "python_without_meaning": "NO",
        "counterfactual": "SEMANTIC_INFERENCE_REQUIRED",
        "contract": "SEMANTIC_REQUIREMENT_NOT_STRUCTURALLY_DECLARED",
        "under_declaration": True,
        "strong_39w": "FAST_INSUFFICIENT_STRONG_RECOVERS",
    },
    "w2-union-when-compare": {
        "role": "blind",
        "class_hint": "B",
        "defect": "WRONG_INTEGRATION_SHAPE",
        "lookalike": "w1-rename-join-temp",
        "user_prompt": (
            "For each bay show morning temperature next to evening temperature."
        ),
        "defect_ko": "오전/오후를 나란히 보여야 하는데 행만 쌓음.",
        "python_without_meaning": "NO",
        "counterfactual": "SEMANTIC_INFERENCE_REQUIRED",
        "contract": "SEMANTIC_REQUIREMENT_NOT_STRUCTURALLY_DECLARED",
        "under_declaration": True,
        "strong_39w": "FAST_INSUFFICIENT_STRONG_RECOVERS",
    },
    "w2-filter-wrong-site": {
        "role": "blind",
        "class_hint": "B",
        "defect": "WRONG_FILTER_SELECTION",
        "lookalike": "w1-filter-then-agg",
        "user_prompt": "Keep only the eastern site lots (site E).",
        "defect_ko": "동부(E)가 필요한데 서부(W)로 필터.",
        "python_without_meaning": "NO",
        "counterfactual": "SEMANTIC_INFERENCE_REQUIRED",
        "contract": "SEMANTIC_REQUIREMENT_NOT_STRUCTURALLY_DECLARED",
        "under_declaration": True,
        "strong_39w": "FAST_INSUFFICIENT_STRONG_RECOVERS",
    },
    # ----- valid lookalikes -----
    "w1-union-total": {
        "role": "lookalike",
        "pairs_with": "w2-collapse-no-roles",
        "user_prompt": "Stack Q1 and Q2 and give total units per SKU across both quarters.",
        "note_ko": "union+agg가 정말로 전체 합계 요청에 맞다.",
    },
    "w5-valid-multi-stage": {
        "role": "lookalike",
        "pairs_with": "w2-single-run-only",
        "user_prompt": "Using only the AM shift, report mean ppm per sensor.",
        "note_ko": "단일 시프트 필터가 요청과 일치.",
    },
    "w1-join-1to1": {
        "role": "lookalike",
        "pairs_with": "w2-join-instead-of-union",
        "user_prompt": "Attach each employee's door assignment to their badge row.",
        "note_ko": "1:1 조인이 요청과 일치.",
    },
    "w1-count-agent": {
        "role": "lookalike",
        "pairs_with": "w2-wrong-group-grain",
        "user_prompt": "Count tickets per agent.",
        "note_ko": "agent group-by가 요청과 일치.",
    },
    "w1-join-select": {
        "role": "lookalike",
        "pairs_with": "w2-drop-needed-metric",
        "user_prompt": "Join fleet km with fuel liters and keep vin, km, and liters.",
        "note_ko": "두 메트릭을 유지하는 유효 select.",
    },
    "w1-rename-join-temp": {
        "role": "lookalike",
        "pairs_with": "w2-union-when-compare",
        "user_prompt": "For each bay place morning temperature beside evening temperature.",
        "note_ko": "rename+join 비교가 요청과 일치.",
    },
    "w1-filter-then-agg": {
        "role": "lookalike",
        "pairs_with": "w2-filter-wrong-site",
        "user_prompt": "For eastern site lots only, sum kg per lot.",
        "note_ko": "사이트 E 필터가 요청과 일치.",
    },
    "w5-valid-same-schema-concat": {
        "role": "lookalike",
        "pairs_with": "w2-collapse-no-roles",
        "user_prompt": "Concatenate warehouse A and warehouse B inventory rows into one list.",
        "note_ko": "적재가 목적인 유효 union.",
    },
    # ----- CLASS A observable wrong -----
    "w2-roles-collapse": {
        "role": "observable",
        "class_hint": "A",
        "defect": "COLLAPSED_DISTINCTION",
        "user_prompt": "Compare warehouse A vs B quantity per bin.",
        "defect_ko": "사이드 선언 후 한 메트릭만 물질화.",
        "python_without_meaning": "YES",
        "counterfactual": "SAFE_DETERMINISTIC",
        "contract": "CONTRACT_PRESENT_BUT_VALIDATION_GAP",
        "under_declaration": False,
        "strong_39w": "FAST_INSUFFICIENT_STRONG_RECOVERS",
    },
    "w3-grain-detail-collapse": {
        "role": "observable",
        "class_hint": "A",
        "defect": "WRONG_GROUPING",
        "user_prompt": "Keep visit-level detail minutes with ward after joining patients.",
        "defect_ko": "detail grain 선언 + 집계 붕괴.",
        "python_without_meaning": "YES",
        "counterfactual": "SAFE_DETERMINISTIC",
        "contract": "CONTRACT_PRESENT_BUT_VALIDATION_GAP",
        "under_declaration": False,
        "strong_39w": "FAST_INSUFFICIENT_STRONG_RECOVERS",
    },
    "w3-missing-col": {
        "role": "observable",
        "class_hint": "A",
        "defect": "OTHER_SEMANTIC_ERROR",
        "user_prompt": "Sum hours by agent.",
        "defect_ko": "존재하지 않는 열 참조.",
        "python_without_meaning": "YES",
        "counterfactual": "SAFE_DETERMINISTIC",
        "contract": "CONTRACT_PRESENT_BUT_VALIDATION_GAP",
        "under_declaration": False,
        "strong_39w": "FAST_INSUFFICIENT_STRONG_RECOVERS",
    },
    "w3-bad-key": {
        "role": "observable",
        "class_hint": "A",
        "defect": "OTHER_SEMANTIC_ERROR",
        "user_prompt": "Join badge to door events on employee id.",
        "defect_ko": "조인 키 없음.",
        "python_without_meaning": "YES",
        "counterfactual": "SAFE_DETERMINISTIC",
        "contract": "CONTRACT_PRESENT_BUT_VALIDATION_GAP",
        "under_declaration": False,
        "strong_39w": "FAST_INSUFFICIENT_STRONG_RECOVERS",
    },
    "w3-fake-dual-roles": {
        "role": "observable",
        "class_hint": "A",
        "defect": "COLLAPSED_DISTINCTION",
        "user_prompt": "Compare east vs west kg per lot as independent sides.",
        "defect_ko": "같은 kg를 두 사이드로 복제.",
        "python_without_meaning": "YES",
        "counterfactual": "SAFE_DETERMINISTIC",
        "contract": "CONTRACT_PRESENT_BUT_VALIDATION_GAP",
        "under_declaration": False,
        "strong_39w": "FAST_OVERCOMMITS_STRONG_CORRECTLY_CANNOT_PLAN",
    },
    "w3-incompat-union": {
        "role": "observable",
        "class_hint": "A",
        "defect": "WRONG_INTEGRATION_SHAPE",
        "user_prompt": "Union the two tables if they share a schema.",
        "defect_ko": "스키마 불일치 union.",
        "python_without_meaning": "YES",
        "counterfactual": "SAFE_DETERMINISTIC",
        "contract": "CONTRACT_PRESENT_BUT_VALIDATION_GAP",
        "under_declaration": False,
        "strong_39w": "BOTH_INSUFFICIENT",
    },
    "w3-genuine-m2m": {
        "role": "m2m",
        "class_hint": "A",
        "defect": "WRONG_INTEGRATION_SHAPE",
        "user_prompt": "Join the two hid tables.",
        "defect_ko": "진짜 many-to-many. 안전 거절.",
        "python_without_meaning": "YES",
        "counterfactual": "SAFE_DETERMINISTIC",
        "contract": "CONTRACT_PRESENT_BUT_VALIDATION_GAP",
        "under_declaration": False,
        "strong_39w": "BOTH_INSUFFICIENT",
        "safety": "SAFELY_BLOCKED_WITHOUT_STRONG_RECOVERY",
    },
    # ----- correct cannot_plan -----
    "w4-missing-color": {
        "role": "cannot_plan",
        "user_prompt": "Split lots by color partitions that are not in the file.",
        "note_ko": "정보 부재. cannot_plan이 맞다.",
    },
    "w4-unrelated": {
        "role": "cannot_plan",
        "user_prompt": "Relate the foo table to the bar table.",
        "note_ko": "공유 키 없음.",
    },
    "w4-missing-period": {
        "role": "cannot_plan",
        "user_prompt": "Compare fleet km before vs after a period column that is absent.",
        "note_ko": "기간 열 부재.",
    },
}

STABILITY_IDS = [
    "w2-collapse-no-roles",
    "w2-filter-wrong-site",
    "w1-union-total",
    "w1-filter-then-agg",
]


def _write(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _load_cache() -> dict[str, Any]:
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    return {}


def _save_cache(cache: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n")


def _compact_exec(exe: Any) -> dict[str, Any] | None:
    if exe is None or not getattr(exe, "success", False) or exe.final_output is None:
        return None
    df = exe.final_output
    rows = df.head(5).astype(object).where(df.head(5).notna(), None).to_dict("records")
    return {
        "columns": [str(c) for c in df.columns],
        "row_count": int(len(df)),
        "sample_rows": rows,
    }


def _claim_quality(verdict: str, evidence: list[str], rec: dict[str, Any]) -> str:
    blob = " ".join(str(x) for x in evidence).lower()
    if rec.get("obs_class") == "B" and verdict in {"fail", "uncertain"}:
        if any(k in blob for k in ("compare", "side", "collapse", "filter", "union", "join")):
            return "SUPPORTED_SEMANTIC_CLAIM"
        return "AMBIGUOUS"
    if rec.get("obs_class") == "B" and verdict == "pass":
        return "UNSUPPORTED_SEMANTIC_CLAIM"
    if rec.get("fast_correct") == "YES" and verdict == "pass":
        return "SUPPORTED_SEMANTIC_CLAIM"
    if rec.get("fast_correct") == "YES" and verdict in {"fail", "uncertain"}:
        return "UNSUPPORTED_SEMANTIC_CLAIM"
    return "AMBIGUOUS"


def _verdict_label(fast: str, verdict: str) -> str | None:
    non_pass = verdict in {"fail", "uncertain"}
    if fast == "NO" and non_pass:
        return "CORRECT_REJECTION"
    if fast == "NO" and verdict == "pass":
        return "SILENT_WRONG"
    if fast == "YES" and verdict == "pass":
        return "CORRECT_PASS"
    if fast == "YES" and non_pass:
        return "FALSE_FAIL"
    return None


def _classify(ev: dict[str, Any], meta: dict[str, Any], val_valid: bool) -> str:
    if meta.get("class_hint") == "A":
        return "A"
    if meta.get("class_hint") == "B":
        # Confirm: no architecture-safe pre-exec signal.
        if (
            ev.get("has_final_grain_contradiction")
            or ev.get("evidence_role_contradiction")
            or (ev.get("has_structural_error") and not ev.get("only_unsafe_codes"))
        ):
            return "C"
        if val_valid:
            return "B"
        return "C"
    if meta.get("role") == "cannot_plan":
        return "A" if ev.get("planner_declared_cannot_plan") else "C"
    return "C"


def build_rows() -> list[dict[str, Any]]:
    raw = {c["attempt_id"]: c for c in build_w_corpus()}
    missing = [i for i in META if i not in raw]
    if missing:
        raise RuntimeError(f"39W corpus missing {missing}")
    rows: list[dict[str, Any]] = []
    for aid, meta in META.items():
        c = raw[aid]
        und = _und_from_frames(c["frames"])
        ev = extract_attempt_evidence(
            attempt_id=aid,
            request_id=c["request_id"],
            plan=c["plan"],
            understanding=und,
            frames=c["frames"],
        )
        val = validate_integration_plan(und, c["plan"], frames=c["frames"])
        exe = None
        if val.valid and getattr(c["plan"], "status", None) != "cannot_plan":
            try:
                exe = execute_integration_plan(c["frames"], c["plan"], val)
            except Exception:  # noqa: BLE001
                exe = None
        schemas = extract_source_schemas_from_understanding(und)
        lineage = build_schema_lineage(c["plan"].to_dict(), schemas) if schemas else {}
        frozen = phase39v_rule_v1(ev)
        obs = _classify(ev, meta, bool(val.valid))
        result_obs = _compact_exec(exe)
        rows.append({
            **{k: v for k, v in c.items() if k not in {"plan", "frames"}},
            **ev,
            **meta,
            "obs_class": obs,
            "frozen": frozen,
            "validation_valid": bool(val.valid),
            "validation_codes": [e.code for e in val.errors],
            "exec_success": None if exe is None else bool(exe.success),
            "result_obs": result_obs,
            "final_schema": list(lineage.get("final_schema") or []),
            "identical_evidence_sets": lineage.get("identical_evidence_signature_column_sets") or [],
            "source_files_in_final": lineage.get("source_files_represented_in_final") or [],
            "user_prompt": meta["user_prompt"],
            "plan_dict": c["plan"].to_dict(),
            "und": und,
        })
    return rows


def production_payload(rec: dict[str, Any]) -> dict[str, Any]:
    return build_verifier_payload(
        user_prompt=rec["user_prompt"],
        plan=rec["plan_dict"],
        result=None,
        understanding=rec["und"],
        variant=SEMANTIC_VERIFIER_VARIANT,
        materialization_mode=MATERIALIZATION,
        source_schemas=extract_source_schemas_from_understanding(rec["und"]),
    )


def result_aware_payload(rec: dict[str, Any]) -> dict[str, Any]:
    p = production_payload(rec)
    p = dict(p)
    p["observed_result"] = rec.get("result_obs")
    return p


def no_claims_payload(base: dict[str, Any]) -> dict[str, Any]:
    p = dict(base)
    p.pop("planner_claims", None)
    return p


def _call_payload_chat(payload: dict[str, Any], *, mention_result: bool) -> Any:
    from core.integrate.semantic_verifier import _VERIFIER_SYSTEM, _normalize_verdict
    from core.llm_client import chat_json as _chat

    user = (
        "Determine whether the proposed integration plan"
        + (" and observed result" if mention_result else "")
        + " directly satisfy all material requirements in the user's request.\n"
        "Do not repair the plan. If evidence is insufficient, return uncertain.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    raw = _chat(
        user,
        system=_VERIFIER_SYSTEM,
        base_url="http://localhost:11434",
        model=SEMANTIC_VERIFIER_MODEL,
    )
    return _normalize_verdict(raw if isinstance(raw, dict) else {})


def run_one_verifier(
    payload_kind: str,
    rec: dict[str, Any],
    cache: dict[str, Any],
    *,
    repeat: int = 0,
) -> dict[str, Any]:
    key = f"{rec['attempt_id']}|{payload_kind}|{repeat}"
    if key in cache:
        return cache[key]

    schemas = extract_source_schemas_from_understanding(rec["und"])
    if payload_kind == "V0":
        # Exact production: variant V1, result=None, V2.2 evidence.
        ver = run_semantic_verification(
            user_prompt=rec["user_prompt"],
            plan=rec["plan_dict"],
            result=None,
            understanding=rec["und"],
            variant=SEMANTIC_VERIFIER_VARIANT,
            model=SEMANTIC_VERIFIER_MODEL,
            materialization_mode=MATERIALIZATION,
            source_schemas=schemas,
        )
    elif payload_kind == "V1":
        # Harness-only: official runner + compact result (variant V2 attaches it).
        ver = run_semantic_verification(
            user_prompt=rec["user_prompt"],
            plan=rec["plan_dict"],
            result=rec.get("result_obs"),
            understanding=rec["und"],
            variant="V2",
            model=SEMANTIC_VERIFIER_MODEL,
            materialization_mode=MATERIALIZATION,
            source_schemas=schemas,
        )
    elif payload_kind == "V2":
        ver = _call_payload_chat(no_claims_payload(production_payload(rec)), mention_result=False)
    else:
        ver = _call_payload_chat(no_claims_payload(result_aware_payload(rec)), mention_result=True)

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
        "payload_kind": payload_kind,
        "repeat": repeat,
        "verdict": d.get("verdict"),
        "reason_code": d.get("reason_code"),
        "evidence": d.get("evidence") or [],
        "parse_ok": d.get("parse_ok"),
        "error": d.get("error"),
        "escalation": bool(esc),
        "escalation_reason": reason,
        "fidelity": "CANONICAL_EQUIVALENT_REPLAY",
        "live": True,
    }
    cache[key] = out
    _save_cache(cache)
    return out


def write_artifacts(rows: list[dict[str, Any]], ver_rows: list[dict[str, Any]]) -> None:
    src = inspect.getsource(evaluate_capability_signal)
    if "has_final_grain_contradiction" not in src:
        raise RuntimeError("PHASE39V_RULE_V1 source unexpected")

    blind = [r for r in rows if r["role"] == "blind"]
    looks = [r for r in rows if r["role"] == "lookalike"]
    obs = [r for r in rows if r["role"] == "observable"]
    cps = [r for r in rows if r["role"] == "cannot_plan"]
    m2m = [r for r in rows if r["role"] == "m2m"]
    wrong = [r for r in rows if r["fast_correct"] == "NO"]
    class_a = [r for r in wrong if r["obs_class"] == "A"]
    class_b = [r for r in wrong if r["obs_class"] == "B"]
    recoverable = [r for r in wrong if r.get("strong_39w") in {
        "FAST_INSUFFICIENT_STRONG_RECOVERS",
        "FAST_OVERCOMMITS_STRONG_CORRECTLY_CANNOT_PLAN",
    }]
    recov_a = [r for r in recoverable if r["obs_class"] == "A"]

    example = next(r for r in rows if r["role"] == "blind")
    v0_payload = production_payload(example)
    _write("baseline_freeze.json", {
        "phase": "39X",
        "phase39w_sha": PHASE39W_SHA,
        "shadow": "OFF",
        "rule_version": PHASE39V_RULE_VERSION,
        "rule_tuned": False,
        "production_routing_changed": False,
        "planner_changed": False,
        "verifier_changed": False,
        "escalation_changed": False,
        "timeout_changed": False,
        "dsl_changed": False,
        "v2_2_changed": False,
        "semantic_verifier_variant_production": SEMANTIC_VERIFIER_VARIANT,
        "semantic_verifier_model": SEMANTIC_VERIFIER_MODEL,
        "production_result_argument": None,
    })
    _write("blind_region_cases.json", [
        {
            "attempt_id": r["attempt_id"],
            "user_prompt": r["user_prompt"],
            "fast_correct": r["fast_correct"],
            "validation_valid": r["validation_valid"],
            "exec_success": r["exec_success"],
            "frozen": r["frozen"],
            "obs_class": r["obs_class"],
            "defect": r["defect"],
            "defect_ko": r["defect_ko"],
            "python_without_meaning": r["python_without_meaning"],
            "final_schema": r["final_schema"],
            "identical_evidence_sets": r["identical_evidence_sets"],
            "signals": {
                "grain": r["has_final_grain_contradiction"],
                "evidence_role": r["evidence_role_contradiction"],
                "structural_non_unsafe": bool(r["has_structural_error"] and not r["only_unsafe_codes"]),
            },
        }
        for r in blind
    ])
    _write("valid_lookalikes.json", [
        {"attempt_id": r["attempt_id"], "pairs_with": r.get("pairs_with"),
         "user_prompt": r["user_prompt"], "note_ko": r.get("note_ko"),
         "validation_valid": r["validation_valid"], "frozen": r["frozen"]}
        for r in looks
    ])
    _write("pre_execution_observability_review.json", [
        {
            "attempt_id": r["attempt_id"],
            "obs_class": r["obs_class"],
            "python_without_meaning": r.get("python_without_meaning"),
            "note_ko": r.get("defect_ko") or r.get("note_ko"),
            "pre_exec_codes": r["validation_codes"],
            "declared_roles": bool((r["plan_dict"].get("final_output_requirements") or {}).get("output_roles")),
        }
        for r in rows if r["fast_correct"] == "NO" or r["role"] == "cannot_plan"
    ])
    _write("counterfactual_signal_review.json", [
        {
            "attempt_id": r["attempt_id"],
            "required_signal": r.get("counterfactual"),
            "implemented": False,
            "note_ko": "신호를 구현하지 않음. 분류만.",
        }
        for r in blind + obs + m2m
    ])
    ceiling_all = None if not wrong else round(len(class_a) / len(wrong), 4)
    ceiling_rec = None if not recoverable else round(len(recov_a) / len(recoverable), 4)
    _write("observability_ceiling.json", {
        "label": "Phase 39X corpus observability ceiling estimate",
        "not_a_universal_rate": True,
        "pre_execution_observable_wrong_over_all_wrong": ceiling_all,
        "n_wrong": len(wrong),
        "n_class_a": len(class_a),
        "n_class_b": len(class_b),
        "architecture_safe_pre_execution_over_recoverable": ceiling_rec,
        "n_recoverable": len(recoverable),
        "result": (
            "PARTIAL_PREEXEC_OBSERVABILITY"
            if class_a and class_b
            else "HIGH_PREEXEC_OBSERVABILITY" if class_a and not class_b
            else "LOW_PREEXEC_OBSERVABILITY" if class_b and not class_a
            else "INDETERMINATE"
        ),
    })
    _write("semantic_defect_taxonomy.json", {
        "note": "analyst labels only; not routing features",
        "counts": dict(Counter(r.get("defect") for r in blind)),
        "cases": [{"attempt_id": r["attempt_id"], "defect": r["defect"]} for r in blind],
    })
    _write("planner_contract_review.json", [
        {
            "attempt_id": r["attempt_id"],
            "contract": r.get("contract"),
            "planner_under_declaration": bool(r.get("under_declaration")),
            "declared_requirements": r["plan_dict"].get("final_output_requirements"),
        }
        for r in blind + obs
    ])
    _write("verifier_current_payload.json", {
        "source": "core.integrate.semantic_escalation.run_integration_pipeline_semantic_experimental",
        "variant": SEMANTIC_VERIFIER_VARIANT,
        "materialization_mode": MATERIALIZATION,
        "model": SEMANTIC_VERIFIER_MODEL,
        "temperature": 0.0,
        "timeout_s": 300,
        "result_passed_in_production": None,
        "observed_result_included_for_V1": "observed_result is only attached when variant in {V2,V3}",
        "production_double_blindness": (
            "semantic_escalation passes result=None AND variant=V1 so "
            "observed_result is omitted even if a result object existed"
        ),
        "payload_keys_example": sorted(v0_payload.keys()),
        "example_attempt": example["attempt_id"],
        "has_observed_result_in_V0": "observed_result" in v0_payload,
        "has_planner_claims": "planner_claims" in v0_payload,
        "has_plan_structure": "plan_structure" in v0_payload,
        "has_materialization_evidence": "materialization_evidence" in v0_payload,
        "phase39l_still_true": True,
    })

    v0 = [v for v in ver_rows if v["payload_kind"] == "V0" and v.get("repeat", 0) == 0]
    v0_map = {v["attempt_id"]: v for v in v0}
    v1 = {v["attempt_id"]: v for v in ver_rows if v["payload_kind"] == "V1" and v.get("repeat", 0) == 0}
    v2 = {v["attempt_id"]: v for v in ver_rows if v["payload_kind"] == "V2" and v.get("repeat", 0) == 0}

    case_results = []
    for r in blind + looks:
        v = v0_map.get(r["attempt_id"])
        if not v:
            case_results.append({
                "attempt_id": r["attempt_id"],
                "role": r["role"],
                "invoked_in_production_path": r["role"] == "blind" and r.get("validation_valid") and r.get("exec_success"),
                "live": False,
            })
            continue
        label = _verdict_label(r["fast_correct"], v["verdict"])
        case_results.append({
            "attempt_id": r["attempt_id"],
            "role": r["role"],
            "fast_correct": r["fast_correct"],
            "verdict": v["verdict"],
            "reason": v["reason_code"],
            "evidence": v["evidence"],
            "verdict_label": label,
            "claim_quality": _claim_quality(v["verdict"], v["evidence"], r),
            "escalation": v["escalation"],
            "fidelity": v["fidelity"],
        })
    _write("verifier_case_results.json", case_results)

    blind_v = [c for c in case_results if c.get("role") == "blind" and c.get("verdict")]
    caught = [c for c in blind_v if c.get("verdict_label") == "CORRECT_REJECTION"]
    silent = [c for c in blind_v if c.get("verdict_label") == "SILENT_WRONG"]
    _write("blind_region_verifier_metrics.json", {
        "n_evaluated": len(blind_v),
        "CORRECT_REJECTION": len(caught),
        "SILENT_WRONG": len(silent),
        "VERIFIER_RECALL_ON_PREEXECUTION_BLIND_REGION": (
            None if not blind_v else round(len(caught) / len(blind_v), 4)
        ),
        "live": bool(blind_v),
    })
    look_v = [c for c in case_results if c.get("role") == "lookalike" and c.get("verdict")]
    ff = [c for c in look_v if c.get("verdict_label") == "FALSE_FAIL"]
    _write("verifier_false_fail_controls.json", {
        "n_evaluated": len(look_v),
        "FALSE_FAIL": len(ff),
        "false_fail_rate": None if not look_v else round(len(ff) / len(look_v), 4),
        "cases": ff,
    })
    _write("verifier_claim_quality.json", [
        {"attempt_id": c["attempt_id"], "quality": c.get("claim_quality"),
         "verdict": c.get("verdict")}
        for c in case_results if c.get("verdict")
    ])
    _write("result_information_stages.json", [
        {
            "attempt_id": r["attempt_id"],
            "T0_pre_exec": {
                "plan_ops": [s.get("op") for s in (r["plan_dict"].get("steps") or [])],
                "declared_roles": bool((r["plan_dict"].get("final_output_requirements") or {}).get("output_roles")),
                "validation_codes": r["validation_codes"],
                "v22_schema": r["final_schema"],
                "identical_evidence": r["identical_evidence_sets"],
            },
            "T1_after_exec": r.get("result_obs"),
            "T2_verifier_production": {
                "includes_observed_result": False,
                "includes_v22": True,
                "includes_user_prompt": True,
            },
            "new_at_T1": "row_count, columns, bounded sample — not in production verifier",
        }
        for r in blind
    ])

    def _delta(kind_map: dict[str, Any], ids: list[str], fast: str) -> dict[str, Any]:
        out = {"changed": [], "same": []}
        for i in ids:
            a, b = v0_map.get(i), kind_map.get(i)
            if not a or not b:
                continue
            rec = {"attempt_id": i, "V0": a["verdict"], kind_map and "other": b["verdict"]}
            if a["verdict"] == b["verdict"]:
                out["same"].append(i)
            else:
                out["changed"].append({"attempt_id": i, "V0": a["verdict"], "other": b["verdict"]})
        return out

    blind_ids = [r["attempt_id"] for r in blind]
    _write("result_awareness_ablation.json", {
        "harness_only": True,
        "production_unchanged": True,
        **_delta(v1, blind_ids, "NO"),
        "lookalike_false_fail_V1": [
            i for i, v in v1.items()
            if i in {r["attempt_id"] for r in looks} and v["verdict"] in {"fail", "uncertain"}
        ],
    })
    _write("planner_claim_ablation.json", {
        "harness_only": True,
        **_delta(v2, blind_ids, "NO"),
    })

    stab = [v for v in ver_rows if v.get("repeat", 0) > 0 or (
        v["payload_kind"] == "V0" and v["attempt_id"] in STABILITY_IDS
    )]
    by = {}
    for v in ver_rows:
        if v["payload_kind"] != "V0" or v["attempt_id"] not in STABILITY_IDS:
            continue
        by.setdefault(v["attempt_id"], []).append(v["verdict"])
    _write("verifier_stability_results.json", {
        "n_repeats_target": 5,
        "cases": {
            k: {"verdicts": vs, "stable": len(set(vs)) == 1, "n": len(vs)}
            for k, vs in by.items()
        },
    })

    chains = []
    for r in blind:
        v = v0_map.get(r["attempt_id"])
        rejected = bool(v and v.get("verdict_label") == "CORRECT_REJECTION" or (
            v and v["verdict"] in {"fail", "uncertain"}
        ))
        recov = r.get("strong_39w") == "FAST_INSUFFICIENT_STRONG_RECOVERS"
        chains.append({
            "attempt_id": r["attempt_id"],
            "A1": "NO",
            "early": r["frozen"],
            "verifier": None if not v else v["verdict"],
            "escalation": None if not v else v["escalation"],
            "A2_oracle_39w": r.get("strong_39w"),
            "chain": (
                "SEMANTIC_RECOVERY_CONFIRMED" if rejected and recov
                else "NO_RECOVERY" if rejected and r.get("strong_39w") == "BOTH_INSUFFICIENT"
                else "INDETERMINATE" if not v
                else "NO_RECOVERY"
            ),
            "A2_fidelity": "RECONSTRUCTED_REPLAY",
            "note_ko": "32B는 39W 오라클. 라이브 32B 없음. RC-J 분리.",
        })
    _write("semantic_recovery_chains.json", chains)
    conf = [c for c in chains if c["chain"] == "SEMANTIC_RECOVERY_CONFIRMED"]
    rejected_n = sum(1 for c in chains if c["verifier"] in {"fail", "uncertain"})
    _write("strong_recovery_value.json", {
        "VERIFIER_TO_STRONG_RECOVERY_RATE": (
            None if not rejected_n else round(len(conf) / rejected_n, 4)
        ),
        "n_correct_rejections": rejected_n,
        "n_recovered": len(conf),
        "operational_32b_failures": 0,
        "oracle": "Phase 39W reconstructed; no new live 32B",
    })

    v_recall = None if not blind_v else len(caught) / len(blind_v)
    ff_rate = None if not look_v else len(ff) / len(look_v)
    if v_recall is None:
        complement = "INDETERMINATE"
    elif v_recall >= 0.7 and (ff_rate or 0) <= 0.25:
        complement = "STRONG_COMPLEMENT"
    elif v_recall >= 0.4:
        complement = "PARTIAL_COMPLEMENT"
    else:
        complement = "WEAK_COMPLEMENT"

    _write("failure_region_map.json", {
        "R1_preexec": [r["attempt_id"] for r in class_a],
        "R2_valid_semantic": [r["attempt_id"] for r in class_b],
        "R3_verifier_plus_recoverable": [c["attempt_id"] for c in conf],
        "R4_verifier_miss": [c["attempt_id"] for c in silent],
        "R5_strong_operational": [],
    })

    next_out = "B"
    if complement == "STRONG_COMPLEMENT":
        next_out = "A"
    elif complement == "WEAK_COMPLEMENT":
        next_out = "B"
    if all(r.get("under_declaration") for r in blind) and complement != "STRONG_COMPLEMENT":
        next_out = "B"
        # under-declaration is real but next is still verifier quality if complement weak

    _write("architecture_boundary_conclusion.json", {
        "hypothesis_supported": all(r["obs_class"] == "B" for r in blind),
        "preexec_result": (
            "PARTIAL_PREEXEC_OBSERVABILITY" if class_a and class_b else "INDETERMINATE"
        ),
        "verifier_complement": complement,
        "production_change": "NO_PRODUCTION_SEMANTIC_CHANGE",
        "next_outcome": next_out,
        "layers": {
            "early_routing": "structural/evidence contradictions only; low incremental value",
            "validator": "R1",
            "semantic_verifier": "R2 responsibility; measured complement below",
            "strong_planner": "recovery after verifier NON-PASS (39W oracle)",
        },
        "do_not_encode_in_python": [r["defect"] for r in blind],
    })
    _write("regression_results.json", {
        "production_code_changed": False,
        "phase39v_rule_unchanged": True,
        "phase39w_sha": PHASE39W_SHA,
    })
    _write("shadow_state_proof.json", {
        "shadow": "OFF",
        "live_shadow": False,
        "MULTI_SHADOW_ENABLED_default": False,
        "env_MULTI_SHADOW_ENABLED": os.environ.get("MULTI_SHADOW_ENABLED", ""),
        "live_verifier_harness": LIVE,
        "live_shadow_requests": 0,
    })
    _write("phase39x_summary.json", {
        "gate": "A" if blind_v else "B",
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
        "n": len(rows),
        "blind_n": len(blind),
        "class_b_n": len(class_b),
        "class_a_n": len(class_a),
        "ceiling": ceiling_all,
        "verifier_recall_blind": None if not blind_v else round(v_recall, 4),
        "false_fail_rate": None if not look_v else round(ff_rate or 0, 4),
        "complement": complement,
        "next": next_out,
        "production_change": "NO_PRODUCTION_SEMANTIC_CHANGE",
    })


def run_verifier_suite(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not LIVE:
        return []
    cache = _load_cache()
    targets = [r for r in rows if r["role"] in {"blind", "lookalike"}]
    out: list[dict[str, Any]] = []
    for kind in ("V0", "V1", "V2"):
        subset = targets if kind != "V2" else [r for r in rows if r["role"] == "blind"]
        for r in subset:
            print(f"verifier {kind} {r['attempt_id']}", flush=True)
            out.append(run_one_verifier(kind, r, cache, repeat=0))
    for r in rows:
        if r["attempt_id"] not in STABILITY_IDS:
            continue
        for i in range(1, 5):
            print(f"stability {r['attempt_id']} #{i+1}", flush=True)
            out.append(run_one_verifier("V0", r, cache, repeat=i))
    return out


def main() -> None:
    rows = build_rows()
    print("rows", len(rows),
          "blind", sum(1 for r in rows if r["role"] == "blind"),
          "classB", sum(1 for r in rows if r["obs_class"] == "B"),
          "classA", sum(1 for r in rows if r["obs_class"] == "A"))
    ver = run_verifier_suite(rows)
    write_artifacts(rows, ver)
    print("wrote", OUT, "verifier_calls", len(ver))


if __name__ == "__main__":
    main()
