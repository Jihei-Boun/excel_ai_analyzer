"""프롬프트 라우팅 공유 타입."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class SingleRouteOutcome:
    reply: str
    dataframe: pd.DataFrame | None
    meta: dict = field(default_factory=dict)
    keep_as_filter: bool = False
    replace_selection: bool = True
    remember_aggregate: bool = False
    aggregate_prompt: str | None = None
    update_context_label: str | None = None
    update_filter_summary: str | None = None
    set_filter_df: pd.DataFrame | None = None
    clear_operation: bool = True
    set_operation_result: object | None = None
    operation_name: str | None = None
    reset_filter: bool = False
    filter_auto_reset: bool = False
