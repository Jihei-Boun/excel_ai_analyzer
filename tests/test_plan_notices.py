"""Plan notices travel from the analysis meta to what the user sees."""

from __future__ import annotations

import pandas as pd

from core.application.headless import _warnings_from_outcome
from core.routing.route_helpers import _merge_analysis_meta
from core.routing.route_types import SingleRouteOutcome


def test_plan_notices_survive_routing_and_become_warnings() -> None:
    meta = _merge_analysis_meta({}, {"plan_notices": ["질문과 다를 수 있습니다."], "retry_log": []})
    assert meta == {"plan_notices": ["질문과 다를 수 있습니다."]}

    outcome = SingleRouteOutcome(reply="", dataframe=pd.DataFrame({"a": [1]}), meta=meta)
    warnings = _warnings_from_outcome(outcome)
    assert [(w.code, w.message) for w in warnings] == [("plan_notice", "질문과 다를 수 있습니다.")]
