"""Phase 40B — research harness only; production verifier frozen."""

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
from tests.benchmark_multi.phase40a_research import ALL_IDS as A40, PROMPT_REGISTRY, prompt_for
from tests.benchmark_multi.phase40b_research import (
    P0_SHA,
    P1_SHA,
    PHASE40A_SHA,
    raw_cases,
)


def test_40a_sha_and_production_frozen() -> None:
    assert PHASE40A_SHA == "9fd1b1009c69fdd8a33383d46f5b434a0ff7af59"
    assert SEMANTIC_VERIFIER_VARIANT == "V1"
    assert SEMANTIC_VERIFIER_MODEL == "qwen2.5:7b"
    assert MAX_RESULT_SAMPLE_ROWS == 5
    assert MAX_RESULT_SAMPLE_COLUMNS == 24
    assert MAX_RESULT_SERIALIZED_CHARS == 4000
    esc = Path("core/integrate/semantic_escalation.py").read_text()
    ver = Path("core/integrate/semantic_verifier.py").read_text()
    assert "observe_result_for_verifier" in esc
    assert "PHASE40B" not in ver
    assert "PHASE40B" not in esc


def test_p0_p1_hashes_match_phase40a() -> None:
    assert PROMPT_REGISTRY["P0"]["sha256"] == P0_SHA
    assert PROMPT_REGISTRY["P1"]["sha256"] == P1_SHA
    from tests.benchmark_multi.phase40b_research import _sha

    assert _sha(prompt_for("P0")) == P0_SHA
    assert _sha(prompt_for("P1")) == P1_SHA
    from tests.benchmark_multi.phase40a_research import P1_ADDENDUM

    assert "group_by" not in P1_ADDENDUM
    assert "tid" not in P1_ADDENDUM


def test_new_corpus_shape_and_independence() -> None:
    cases = raw_cases()
    ids = [c["attempt_id"] for c in cases]
    assert len(ids) >= 36
    assert len(ids) == len(set(ids))
    assert set(ids).isdisjoint(set(A40))
    yes = sum(c["fast_correct"] == "YES" for c in cases)
    no = sum(c["fast_correct"] == "NO" for c in cases)
    assert 0.55 <= yes / len(cases) <= 0.70
    assert 0.25 <= no / len(cases) <= 0.40
    grouping_wrong = [c for c in cases if c.get("defect") == "grouping_identity"]
    assert len(grouping_wrong) >= 4
    nong = [c for c in cases if c["fast_correct"] == "NO" and c.get("defect") != "grouping_identity"]
    assert len(nong) >= len([c for c in cases if c["fast_correct"] == "NO"]) / 2
    trunc = [c for c in cases if c.get("trunc")]
    assert len(trunc) >= 3
    assert any(c["sources"] == 1 for c in cases)
    assert any(c["sources"] == 2 for c in cases)
    p1 = prompt_for("P1")
    for banned in ("group_by", "this case should fail", "b40-n-campus"):
        assert banned not in p1
