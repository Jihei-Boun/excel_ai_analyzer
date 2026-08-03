"""범용 LLM 스키마→계획→엔진→검증 통합 파이프라인."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from core.constants import MERGES_DIR
from core.llm_client import chat_json
from core.plan_builder import build_execution_plan
from core.plan_engine import execute_plan, export_sheets_xlsx, sheets_to_xlsx_bytes
from core.plan_types import ExecutionPlan, FileSchema, IntegrateResult, ValidationReport
from core.plan_validate import validate_integrate_result
from core.schema_infer import infer_schemas
from core.text_normalize import normalize_text


_STRUCTURAL_INTEGRATE_HINTS = (
    "통합",
    "병합",
    "합쳐",
    "합쳐서",
    "하나로 만",
    "한 파일로",
    "한파일로",
    "aggregate_merge",
    "merge files",
    "integrate",
    "combined workbook",
    "통합결과",
    "통합해",
    "병합해",
)

_EXAMPLE_NAME_HINTS = (
    "통합결과",
    "integrated",
    "golden",
    "expected",
    "정답",
)


def looks_like_structural_integrate(prompt: str) -> bool:
    """다중 파일 구조적 통합 요청인지 — 도메인 전용이 아닌 범용 힌트."""
    text = normalize_text(prompt)
    raw = str(prompt or "").lower()
    if any(hint in text for hint in _STRUCTURAL_INTEGRATE_HINTS):
        return True
    return any(hint in raw for hint in ("merge", "integrate", "aggregate_merge"))


def split_sources_and_examples(
    named_frames: list[tuple[str, pd.DataFrame]],
) -> tuple[list[tuple[str, pd.DataFrame]], list[tuple[str, pd.DataFrame]]]:
    """파일명 힌트로 few-shot 예시와 소스를 분리 (하드코딩 스키마 없음)."""
    sources: list[tuple[str, pd.DataFrame]] = []
    examples: list[tuple[str, pd.DataFrame]] = []
    for name, frame in named_frames:
        lowered = name.lower()
        norm = normalize_text(name)
        if any(hint in norm or hint in lowered for hint in _EXAMPLE_NAME_HINTS):
            examples.append((name, frame))
        else:
            sources.append((name, frame))
    if len(sources) < 2 and examples:
        # 예시만 있고 소스가 부족하면 전부 소스로 취급
        return named_frames, []
    return sources, examples


def run_integrate_pipeline(
    prompt: str,
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    base_url: str,
    model: str,
    use_budget_profile: bool = False,
    max_retries: int = 1,
    export: bool = True,
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
    schemas: dict[str, FileSchema] | None = None,
    plan: ExecutionPlan | None = None,
) -> IntegrateResult:
    """
    1) 스키마 추론 2) 실행 계획 3) 엔진 4) 검증 5) 실패 시 재추론.
    도메인 전용 integrator를 호출하지 않는다.
    """
    json_fn = chat_json_fn or chat_json
    sources, examples = split_sources_and_examples(named_frames)
    if len(sources) < 2:
        raise ValueError("구조적 통합에는 소스 파일이 2개 이상 필요합니다.")

    source_map = {name: frame.copy() for name, frame in sources}
    previous_errors: list[str] = []
    last_validation: ValidationReport | None = None
    last_plan: ExecutionPlan | None = plan
    last_schemas: dict[str, FileSchema] = schemas or {}
    last_exec: dict[str, Any] | None = None

    attempts = max(1, max_retries + 1)
    for attempt in range(attempts):
        if schemas is None or attempt > 0:
            last_schemas = infer_schemas(
                sources,
                base_url=base_url,
                model=model,
                use_budget_profile=use_budget_profile,
                example_frames=examples or None,
                chat_json_fn=json_fn,
            )
        else:
            last_schemas = schemas

        if plan is None or attempt > 0:
            last_plan = build_execution_plan(
                prompt,
                named_frames=sources,
                schemas=last_schemas,
                base_url=base_url,
                model=model,
                use_budget_profile=use_budget_profile,
                example_frames=examples or None,
                previous_errors=previous_errors or None,
                chat_json_fn=json_fn,
            )
        else:
            last_plan = plan

        assert last_plan is not None
        last_exec = execute_plan(last_plan, source_map)
        last_validation = validate_integrate_result(
            plan=last_plan,
            source_details=last_exec["source_details"],
            integrated_details=last_exec["integrated_details"],
            integrated=last_exec["integrated"],
        )
        if last_validation.ok:
            break
        previous_errors = [issue.message for issue in last_validation.errors]
        # 다음 루프에서 강제 재추론
        schemas = None
        plan = None

    assert last_exec is not None and last_plan is not None and last_validation is not None

    workbook_bytes = None
    workbook_path = None
    if export and last_validation.ok:
        workbook_bytes = sheets_to_xlsx_bytes(last_exec["sheets"])
        MERGES_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workbook_path = str(MERGES_DIR / f"integrated_{stamp}.xlsx")
        export_sheets_xlsx(last_exec["sheets"], path=workbook_path)
    elif last_validation.ok:
        workbook_bytes = sheets_to_xlsx_bytes(last_exec["sheets"])

    reply = _build_reply(last_plan, last_validation, workbook_path)
    meta = {
        "integrate_plan": last_plan.to_dict(),
        "integrate_validation": last_validation.summary_text(),
        "workbook_path": workbook_path,
        "workbook_bytes": workbook_bytes,
        "workbook_sheets": list(last_exec["sheets"].keys()) if last_validation.ok else [],
    }
    return IntegrateResult(
        integrated=last_exec["integrated"],
        sheets=last_exec["sheets"],
        plan=last_plan,
        schemas=last_schemas,
        validation=last_validation,
        workbook_path=workbook_path,
        workbook_bytes=workbook_bytes,
        reply=reply,
        meta=meta,
    )


def try_integrate_pipeline(
    prompt: str,
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    base_url: str,
    model: str,
    use_budget_profile: bool = False,
) -> IntegrateResult | None:
    """요청이 구조적 통합이면 파이프라인을 실행하고, 아니면 None."""
    if len(named_frames) < 2:
        return None
    if not looks_like_structural_integrate(prompt):
        return None
    return run_integrate_pipeline(
        prompt,
        named_frames,
        base_url=base_url,
        model=model,
        use_budget_profile=use_budget_profile,
    )


def _build_reply(
    plan: ExecutionPlan,
    validation: ValidationReport,
    workbook_path: str | None,
) -> str:
    sheet_bits = []
    if plan.include_normalized_source_sheets:
        sheet_bits.append("원본 정규화 시트")
    sheet_bits.append(f"'{plan.integrated_sheet_name}' 통합 시트")
    status = "검증 통과" if validation.ok else "검증 경고/실패 포함"
    path_text = f" 저장: `{workbook_path}`" if workbook_path else ""
    warn_text = ""
    if validation.warnings:
        warn_text = " · " + "; ".join(w.message for w in validation.warnings[:3])
    err_text = ""
    if validation.errors:
        err_text = " · 오류: " + "; ".join(e.message for e in validation.errors[:3])
    return (
        f"구조화 실행 계획({plan.operation})으로 파일을 통합했습니다 "
        f"({', '.join(sheet_bits)}). {status}.{path_text}{warn_text}{err_text}"
    )
