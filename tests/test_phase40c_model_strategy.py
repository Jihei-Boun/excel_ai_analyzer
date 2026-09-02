"""Phase 40C — research freeze tests. Production verifier unchanged."""

from __future__ import annotations

from pathlib import Path

from core.integrate.result_observation import (
    MAX_RESULT_SAMPLE_COLUMNS,
    MAX_RESULT_SAMPLE_ROWS,
    MAX_RESULT_SERIALIZED_CHARS,
)
from core.integrate.semantic_escalation import (
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
)
from tests.benchmark_multi.phase40a_research import ALL_IDS as A40
from tests.benchmark_multi.phase40b_research import raw_cases as b40_raw
from tests.benchmark_multi.phase40c_research import PHASE40B_SHA, fresh_raw


def test_40b_sha_and_production_frozen() -> None:
    assert PHASE40B_SHA == "faa9d2606636170db2eb6643325d8489371d63c7"
    assert SEMANTIC_VERIFIER_VARIANT == "V1"
    assert SEMANTIC_VERIFIER_MODEL == "qwen2.5:7b"
    assert MAX_RESULT_SAMPLE_ROWS == 5
    assert MAX_RESULT_SAMPLE_COLUMNS == 24
    assert MAX_RESULT_SERIALIZED_CHARS == 4000
    esc = Path("core/integrate/semantic_escalation.py").read_text()
    ver = Path("core/integrate/semantic_verifier.py").read_text()
    assert "PHASE40C" not in ver
    assert "PHASE40C" not in esc
    assert "observe_result_for_verifier" in esc


def test_fresh_holdout_shape_and_independence() -> None:
    cases = fresh_raw()
    ids = [c["attempt_id"] for c in cases]
    assert len(ids) >= 30
    assert len(ids) == len(set(ids))
    b40 = {c["attempt_id"] for c in b40_raw()}
    assert set(ids).isdisjoint(set(A40))
    assert set(ids).isdisjoint(b40)
    yes = sum(c["fast_correct"] == "YES" for c in cases)
    no = sum(c["fast_correct"] == "NO" for c in cases)
    assert 0.60 <= yes / len(cases) <= 0.75
    assert 0.20 <= no / len(cases) <= 0.35
    assert any(c["trunc"] for c in cases)
    assert any(c["sources"] == 1 for c in cases)
    assert any(c["sources"] == 2 for c in cases)
    assert any(c["fast_correct"] == "NO" and c.get("defect") != "grouping_identity" for c in cases)


def test_p1_not_in_40c_strategies() -> None:
    text = Path("tests/benchmark_multi/phase40c_research.py").read_text()
    assert "8B + production P0" in text
    assert "V0" in text and "V1" in text
    assert "qwen3:8b" in text
