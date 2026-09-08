"""Headless application boundary for Excel AI Analyzer.

Public symbols intended for Coding Agent / subprocess callers.
"""

from core.application.contracts import (
    CLI_EXIT_CODES,
    CONTRACT_VERSION,
    AnalyzeRequest,
    AnalyzeResponse,
    ContractError,
)
from core.application.headless import analyze_excel, parse_analyze_request
from core.application.transform_contracts import (
    TRANSFORM_CONTRACT_VERSION,
    TRANSFORM_OPERATION,
    ExtractToSheetOp,
    TransformRequest,
    TransformResponse,
    UnmergeCellsOp,
    parse_transform_request,
)
from core.application.transform_headless import transform_excel
from core.application.transform_prompt_contracts import (
    TransformPromptRequest,
    parse_transform_prompt_request,
)
from core.application.transform_prompt_headless import transform_excel_from_prompt

__all__ = [
    "CONTRACT_VERSION",
    "CLI_EXIT_CODES",
    "AnalyzeRequest",
    "AnalyzeResponse",
    "ContractError",
    "analyze_excel",
    "parse_analyze_request",
    "transform_excel",
    "transform_excel_from_prompt",
    "TRANSFORM_CONTRACT_VERSION",
    "TRANSFORM_OPERATION",
    "ExtractToSheetOp",
    "TransformRequest",
    "TransformPromptRequest",
    "TransformResponse",
    "UnmergeCellsOp",
    "parse_transform_request",
    "parse_transform_prompt_request",
]
