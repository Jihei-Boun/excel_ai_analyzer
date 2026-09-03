"""Post-Phase-40 residual measurement harness freeze (research only).

Does not run live models. Confirms production baseline is untouched and
the fresh corpus is not an exact clone of prior representative cases.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from core.integrate.semantic_escalation import SEMANTIC_VERIFIER_MODEL, SEMANTIC_VERIFIER_VARIANT
from core.shadow.config import load_shadow_config
from tests.benchmark_multi.phase40_residual import (
    build_fresh_corpus,
    corpus_manifest,
    production_config,
)


def test_production_baseline_untouched() -> None:
    assert SEMANTIC_VERIFIER_MODEL == "qwen2.5:7b"
    assert SEMANTIC_VERIFIER_VARIANT == "V1"
    assert not load_shadow_config().enabled
    cfg = production_config()
    assert cfg.verifier_model == "qwen2.5:7b"
    assert cfg.strong_model == "qwen3:32b"
    assert cfg.reverify_strong is False
    for rel in (
        "core/integrate/semantic_escalation.py",
        "core/integrate/semantic_verifier.py",
        "core/integrate/integration_plan_validate.py",
        "core/routing/route_multi.py",
        "core/shadow/config.py",
    ):
        text = Path(rel).read_text()
        assert "SemanticRequirementContract" not in text
        assert "r40-A01" not in text


def test_fresh_corpus_size_and_balance() -> None:
    cases = build_fresh_corpus()
    assert 24 <= len(cases) <= 40
    ids = [c["case_id"] for c in cases]
    assert len(ids) == len(set(ids))
    cats = Counter(c["category"] for c in cases)
    for required in (
        "straightforward_valid",
        "grain_sensitive",
        "join_vs_union",
        "independent_evidence",
        "cannot_plan",
        "filter_sensitive_join",
        "multi_step",
    ):
        assert cats[required] >= 3
    expected = Counter(c["manual_expected_outcome"] for c in cases)
    assert expected["YES"] >= 18
    assert expected["CORRECT_CANNOT_PLAN"] == cats["cannot_plan"]


def test_not_prior_exact_clones() -> None:
    for case in corpus_manifest():
        assert case["case_id"].startswith("r40-")
        joined = " ".join(
            [
                case["case_id"],
                case["user_prompt"],
                " ".join(case["source_files"]),
                case["difficulty_notes"],
            ]
        ).lower()
        assert "campus" not in joined
        assert "desk_id" not in joined
        assert "reed_id" not in joined
        assert "p39s" not in joined
        assert "entity_id" not in " ".join(case["source_files"]).lower() or case["category"] != "filter_sensitive_join"
