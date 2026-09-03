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

__all__ = [
    "CONTRACT_VERSION",
    "CLI_EXIT_CODES",
    "AnalyzeRequest",
    "AnalyzeResponse",
    "ContractError",
    "analyze_excel",
    "parse_analyze_request",
]
