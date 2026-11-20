"""Fallback answers carry a visible warning in the web chat, decided the same way as headless."""

from __future__ import annotations

from core.routing.route_helpers import is_unverified_fallback, unverified_fallback_notice


def test_fallback_paths_are_unverified() -> None:
    for reason in ("retrieval_fallback", "simple_groupby_fallback", "pandasai_final_fallback"):
        assert is_unverified_fallback(reason)
    assert is_unverified_fallback(None, "PandasAI")
    assert not is_unverified_fallback(None)
    assert not is_unverified_fallback("plan_validation_exhausted")


def test_notice_names_the_fallback_used() -> None:
    assert "질문에 나온 값으로 행을 찾는 방식" in unverified_fallback_notice("retrieval_fallback")
    assert unverified_fallback_notice(None).startswith("검증되지 않은 답입니다.")
