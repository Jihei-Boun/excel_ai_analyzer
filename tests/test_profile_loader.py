"""프로필 YAML 로딩 단위 테스트."""

from __future__ import annotations

from core import constants
from core.profile_loader import (
    PROFILES_DIR,
    active_profile,
    clear_profile_cache,
    list_profile_names,
    load_budget_profile,
    load_column_hints,
    load_column_meanings,
    load_generic_profile,
    load_meaning_rules,
    load_profile,
)


def test_sales_profile_in_list() -> None:
    clear_profile_cache()
    assert "sales" in list_profile_names()
    sales = load_profile("sales")
    assert sales["domain"] == "sales"
    assert sales["enable_column_prefs"] is False
    assert "상품명" in sales["preferred_labels"]


def test_use_profile_context() -> None:
    from core.profile_loader import preferred_labels_for, use_profile

    clear_profile_cache()
    with use_profile("sales"):
        assert preferred_labels_for()[0] in {"상품명", "제품명", "고객", "고객명", "지역", "채널", "카테고리"}
    # 컨텍스트 종료 후 generic
    assert "비목분류" not in preferred_labels_for(profile_name="generic")


def test_profiles_dir_exists() -> None:
    assert (PROFILES_DIR / "column_hints.yaml").is_file()
    assert (PROFILES_DIR / "budget.yaml").is_file()
    assert (PROFILES_DIR / "generic.yaml").is_file()
    assert (PROFILES_DIR / "column_meanings.yaml").is_file()


def test_column_hints_load() -> None:
    clear_profile_cache()
    hints = load_column_hints()
    assert "매출" in hints["amount_column_hints"]
    assert "금액" in hints["amount_column_hints"]
    # 예산 전용 금액 토큰은 공유 힌트에 두지 않는다
    assert "예산" not in hints["amount_column_hints"]
    assert "집행" not in hints["amount_column_hints"]
    # 비용명·세목은 공유가 아니라 budget extras
    assert "비용명" not in hints["item_column_hints"]
    assert "세목" not in hints["item_column_hints"]
    assert "비용명" not in hints["code_metric_name_hints"]
    assert "세목" not in hints["code_column_hints"]
    assert hints["group_column_suffixes"]


def test_budget_column_hint_extras_merge() -> None:
    from core.profile_loader import column_hints_for

    clear_profile_cache()
    merged = column_hints_for(profile_name="budget")
    assert "예산" in merged["amount_column_hints"]
    assert "집행" in merged["amount_column_hints"]
    assert "비목분류" in merged["group_column_hints"]
    assert "비용명" in merged["item_column_hints"]
    assert "세목" in merged["code_column_hints"]
    assert "비용명" in merged["code_metric_name_hints"]
    generic = column_hints_for(profile_name="generic")
    assert "예산" not in generic["amount_column_hints"]
    assert "비목분류" not in generic["group_column_hints"]
    assert "비용명" not in generic["item_column_hints"]

def test_suggest_profile_name_budget_and_sales() -> None:
    import pandas as pd
    from core.profile_loader import clear_profile_cache, suggest_profile_name

    clear_profile_cache()
    budget_df = pd.DataFrame(
        {
            "비목분류": ["A"],
            "계획예산": [1],
            "실행예산": [2],
            "집행계": [3],
        }
    )
    name, score = suggest_profile_name(budget_df)
    assert name == "budget"
    assert score >= 2

    sales_df = pd.DataFrame(
        {
            "상품명": ["X"],
            "매출": [100],
            "수량": [2],
            "고객명": ["Y"],
        }
    )
    name, score = suggest_profile_name(sales_df)
    assert name == "sales"
    assert score >= 2

    generic_df = pd.DataFrame({"col_a": [1], "col_b": [2]})
    name, score = suggest_profile_name(generic_df)
    assert name == "generic"


def test_budget_roles_and_column_prefs() -> None:
    clear_profile_cache()
    budget = load_budget_profile()
    assert budget["roles"]["metric_numerator"]
    assert budget["roles"]["metric_denominator"]
    assert budget["label_columns"] == budget["item_column_candidates"]
    assert budget["metric_numerator"] == budget["executed_column_candidates"]
    prefs = budget["column_prefs"]
    assert prefs.get("rate_name") == "집행률"
    assert "집행계_합계" in prefs.get("default_numerator")
    assert budget["summary_builder"] == "budget"


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


def test_generic_profile_has_no_column_prefs() -> None:
    clear_profile_cache()
    generic = load_generic_profile()
    assert generic["enable_column_prefs"] is False
    assert generic["footer_labels"] == ()
    assert "비목분류" not in generic["preferred_labels"]
    assert generic["plan_guidance"] == ""
    assert generic["structured_analysis_keywords"] == ()
    assert generic["interpret_guidance"] == ""


def test_budget_profile_enables_prefs_and_guidance() -> None:
    clear_profile_cache()
    budget = load_budget_profile()
    assert budget["enable_column_prefs"] is True
    assert "내부흡수액" in budget["footer_labels"]
    assert "비목분류" in budget["preferred_labels"]
    assert "집행계_합계" in budget["plan_guidance"]
    assert "집행률" in budget["structured_analysis_keywords"]
    assert "집행률" in budget["complex_analysis_keywords"]
    assert budget["display_labels"].get("rate") == "집행률"
    assert "가집행" in budget["interpret_guidance"]


def test_profile_column_helpers() -> None:
    from core.profile_loader import (
        default_rate_name,
        detail_label_columns_present,
        display_labels_for,
        preferred_columns_present,
        related_metric_columns_present,
        use_profile,
    )

    clear_profile_cache()
    cols = {"비목분류", "비용명_2", "비용명", "집행계_합계", "실행예산_합계"}
    with use_profile("budget"):
        assert preferred_columns_present(cols)[0] == "비목분류"
        assert "비용명_2" in detail_label_columns_present(cols)
        assert "집행계_합계" in related_metric_columns_present(cols)
        assert default_rate_name() == "집행률"
        assert display_labels_for()["zero_rate_count"] == "0%미집행수"
    with use_profile("generic"):
        assert default_rate_name() == "비율"
        assert preferred_columns_present({"항목명", "매출"}) == ["항목명"]


def test_structured_keywords_profile_scoped() -> None:
    from core.prompt_intent import expects_dataframe, wants_structured_analysis
    from core.profile_loader import use_profile
    from core.text_normalize import keyword_in_text

    clear_profile_cache()
    # '행'이 한글 단어 중간에 있어도 오탐하지 않음
    assert keyword_in_text("집행률", "행") is False
    assert expects_dataframe("집행률") is False
    assert keyword_in_text("표로 보여줘", "표") is True
    assert keyword_in_text("비용명별 합계", "별") is True

    with use_profile("generic"):
        assert wants_structured_analysis("잔액") is False
        assert wants_structured_analysis("비중") is False
        assert wants_structured_analysis("집행률") is False
    with use_profile("budget"):
        assert wants_structured_analysis("잔액") is True
        assert wants_structured_analysis("비중") is True
        assert wants_structured_analysis("집행률") is True
    # 범용 키워드는 프로필과 무관
    assert wants_structured_analysis("상관관계를 분석해줘", profile_name="generic") is True


def test_guardrail_hints_and_summary_registry() -> None:
    from core.profile_loader import guardrail_hints_for, use_profile
    from core.summary_builders import list_summary_builders, run_summary_builder
    import pandas as pd

    clear_profile_cache()
    with use_profile("generic"):
        hints = guardrail_hints_for()
        assert hints["code_col"] == "코드"
        assert hints["name_col"] == "명칭"
    with use_profile("budget"):
        hints = guardrail_hints_for()
        assert hints["code_col"] == "비용명"
        assert hints["name_col"] == "비용명_2"
        assert hints["group_col"] == "비목분류"

    assert "generic" in list_summary_builders()
    assert "budget" in list_summary_builders()
    text = run_summary_builder(
        "generic",
        pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}),
        file_name="t.csv",
        sheets=[],
    )
    assert "a" in text or "행" in text or "컬럼" in text


def test_list_and_load_custom_profile(tmp_path, monkeypatch) -> None:
    """profiles에 YAML만 추가하면 로드 가능해야 한다."""
    import core.profile_loader as pl

    monkeypatch.setattr(pl, "PROFILES_DIR", tmp_path)
    (tmp_path / "column_hints.yaml").write_text(
        "group_column_hints: []\n"
        "group_column_suffixes: []\n"
        "group_column_exact: []\n"
        "item_column_hints: []\n"
        "code_column_hints: []\n"
        "code_metric_name_hints: []\n"
        "amount_column_hints: []\n",
        encoding="utf-8",
    )
    (tmp_path / "column_meanings.yaml").write_text("meanings: []\n", encoding="utf-8")
    (tmp_path / "sales.yaml").write_text(
        "summary: sales\n"
        "currency: none\n"
        "domain: sales\n"
        "enable_column_prefs: false\n"
        "preferred_labels: [상품명, 고객]\n"
        "footer_labels: []\n"
        "plan_guidance: ''\n"
        "suggested_prompts: [매출 합계를 구해줘]\n",
        encoding="utf-8",
    )
    pl.clear_profile_cache()
    assert "sales" in pl.list_profile_names()
    sales = pl.load_profile("sales")
    assert sales["name"] == "sales"
    assert sales["preferred_labels"] == ("상품명", "고객")
    assert sales["enable_column_prefs"] is False


def test_load_profile_and_active() -> None:
    clear_profile_cache()
    assert load_profile("generic")["name"] == "generic"
    assert load_profile("budget")["name"] == "budget"
    assert active_profile(profile_name="generic")["name"] == "generic"
    assert active_profile(profile_name="budget")["name"] == "budget"
    # deprecated shim
    assert active_profile(profile_name="generic")["name"] == "generic"
    assert active_profile(profile_name="budget")["name"] == "budget"


def test_column_meanings_and_merge() -> None:
    clear_profile_cache()
    generic = load_column_meanings()
    assert generic
    assert any("식별자" in meaning for _, meaning in generic)

    merged = load_meaning_rules(profile_name="budget")
    assert len(merged) >= len(generic)
    # budget 규칙이 앞에 오므로 실행예산은 예산 문구
    from core.schema_compare import estimate_column_meaning

    budget_meaning = estimate_column_meaning("실행예산", profile_name="budget")
    assert "예산" in budget_meaning
    generic_meaning = estimate_column_meaning("매출", profile_name="generic")
    assert "금액" in generic_meaning


def test_constants_reexport_matches_yaml() -> None:
    clear_profile_cache()
    hints = load_column_hints()
    budget = load_budget_profile()
    # constants는 import 시점에 로드되므로 값이 동일한지만 확인
    assert set(constants.AMOUNT_COLUMN_HINTS) == set(hints["amount_column_hints"])
    assert set(constants.BUDGET_FOOTER_LABELS) == set(budget["footer_labels"])
    assert constants.BUDGET_DETECT_MIN_HITS == budget["detect_min_hits"]
