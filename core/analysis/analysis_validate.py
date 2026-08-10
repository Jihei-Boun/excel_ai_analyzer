"""분석 검증 facade — plan-time / result-time 분리 재수출.

- Plan validation: ``analysis_plan_validate.validate_analysis_plan``
- Result validation: ``analysis_result_validate.validate_analysis_result``
"""

from __future__ import annotations

from core.analysis.analysis_plan_validate import (
    format_plan_validation_feedback,
    suggest_column_candidates,
    validate_analysis_plan,
    validation_error_messages as plan_validation_error_messages,
)
from core.analysis.analysis_result_validate import (
    format_result_validation_feedback,
    validate_analysis_result,
    validation_error_messages as result_validation_error_messages,
    validation_info_messages,
    validation_warning_messages,
)


def validation_error_messages(report):  # noqa: ANN001 — 하위 호환
    """result/plan 공통: error 메시지 목록."""
    return result_validation_error_messages(report)


__all__ = [
    "validate_analysis_plan",
    "validate_analysis_result",
    "format_plan_validation_feedback",
    "format_result_validation_feedback",
    "suggest_column_candidates",
    "validation_error_messages",
    "validation_warning_messages",
    "validation_info_messages",
    "plan_validation_error_messages",
    "result_validation_error_messages",
]
