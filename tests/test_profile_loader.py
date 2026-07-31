"""프로필 YAML 로딩 단위 테스트."""

from __future__ import annotations

from core import constants
from core.profile_loader import (
    PROFILES_DIR,
    active_profile,
    clear_profile_cache,
    load_budget_profile,
    load_column_hints,
    load_column_meanings,
    load_generic_profile,
    load_meaning_rules,
    load_profile,
)


def test_profiles_dir_exists() -> None:
    assert (PROFILES_DIR / "column_hints.yaml").is_file()
    assert (PROFILES_DIR / "budget.yaml").is_file()
    assert (PROFILES_DIR / "generic.yaml").is_file()
    assert (PROFILES_DIR / "column_meanings.yaml").is_file()


def test_column_hints_load() -> None:
    clear_profile_cache()
    hints = load_column_hints()
    assert "예산" in hints["amount_column_hints"]
    assert "매출" in hints["amount_column_hints"]
    assert "비용명" in hints["item_column_hints"]
    assert hints["group_column_suffixes"]


def test_budget_profile_load() -> None:
    clear_profile_cache()
    budget = load_budget_profile()
    assert budget["detect_min_hits"] >= 1
    assert "계획예산" in budget["column_hints"]
    assert "내부흡수액" in budget["footer_labels"]
    assert "예실대비표" in budget["intro"]
    assert budget["currency"] == "krw"
    assert budget["summary"] == "budget"
    assert budget["suggested_prompts"]
    assert budget["meanings"]


def test_generic_profile_load() -> None:
    clear_profile_cache()
    generic = load_generic_profile()
    assert generic["name"] == "generic"
    assert generic["currency"] == "none"
    assert generic["summary"] == "generic"
    assert generic["suggested_prompts"]
    assert "파일을 요약해줘" in generic["suggested_prompts"]


def test_load_profile_and_active() -> None:
    clear_profile_cache()
    assert load_profile("generic")["name"] == "generic"
    assert load_profile("budget")["name"] == "budget"
    assert active_profile(use_budget_profile=False)["name"] == "generic"
    assert active_profile(use_budget_profile=True)["name"] == "budget"


def test_column_meanings_and_merge() -> None:
    clear_profile_cache()
    generic = load_column_meanings()
    assert generic
    assert any("식별자" in meaning for _, meaning in generic)

    merged = load_meaning_rules(use_budget_profile=True)
    assert len(merged) >= len(generic)
    # budget 규칙이 앞에 오므로 실행예산은 예산 문구
    from core.schema_compare import estimate_column_meaning

    budget_meaning = estimate_column_meaning("실행예산", use_budget_profile=True)
    assert "예산" in budget_meaning
    generic_meaning = estimate_column_meaning("매출", use_budget_profile=False)
    assert "금액" in generic_meaning


def test_constants_reexport_matches_yaml() -> None:
    clear_profile_cache()
    hints = load_column_hints()
    budget = load_budget_profile()
    # constants는 import 시점에 로드되므로 값이 동일한지만 확인
    assert set(constants.AMOUNT_COLUMN_HINTS) == set(hints["amount_column_hints"])
    assert set(constants.BUDGET_FOOTER_LABELS) == set(budget["footer_labels"])
    assert constants.BUDGET_DETECT_MIN_HITS == budget["detect_min_hits"]
