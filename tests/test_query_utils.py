"""Tests for extract_symbols_from_query — symbol extraction from NL queries."""

from pycodegraph.search.query_utils import extract_symbols_from_query, is_test_query


class TestExtractSymbolsFromQuery:
    """Verify symbol extraction through the public interface."""

    def test_dot_notation_extracts_both_sides(self) -> None:
        """CamelCase._underscore_attr should yield both sides."""
        result = extract_symbols_from_query(
            "QuerySet._fetch_all SQL query compiler execute"
        )
        assert "_fetch_all" in result, f"_fetch_all missing from {result}"
        assert "QuerySet" in result

    def test_standalone_underscore_prefix_snake_case(self) -> None:
        """_private_method standing alone should be extracted."""
        result = extract_symbols_from_query("_private_method called")
        assert "_private_method" in result

    def test_dot_notation_preserves_qualified_name(self) -> None:
        """QuerySet._fetch_all should also add the qualified name as a whole."""
        result = extract_symbols_from_query("QuerySet._fetch_all")
        assert "QuerySet._fetch_all" in result

    def test_double_underscore_dunder(self) -> None:
        """__init__ and __str__ should be extractable."""
        result = extract_symbols_from_query("Model.__init__ and __str__")
        assert "__init__" in result
        assert "__str__" in result

    def test_dot_notation_dunder(self) -> None:
        """ClassName.__init__ should extract both sides and qualified name."""
        result = extract_symbols_from_query("ClassName.__init__")
        assert "ClassName" in result
        assert "__init__" in result
        assert "ClassName.__init__" in result

    def test_plain_names_still_extracted(self) -> None:
        """Regression: normal CamelCase and snake_case still work."""
        result = extract_symbols_from_query("UserService get_user_by_id MAX_RETRIES")
        assert "UserService" in result
        assert "get_user_by_id" in result
        assert "MAX_RETRIES" in result

    def test_single_underscore_not_extracted(self) -> None:
        """A bare '_' should not appear in results — too short to be a symbol."""
        result = extract_symbols_from_query("_ something")
        assert "_" not in result

    def test_dot_notation_regular_attr(self) -> None:
        """app.isPackaged (non-underscore attr) still works after regex change."""
        result = extract_symbols_from_query("app.isPackaged")
        assert "isPackaged" in result
        assert "app.isPackaged" in result

    def test_non_dunder_double_underscore_not_extracted(self) -> None:
        """__partial (no trailing __) is not a dunder — should not be extracted."""
        result = extract_symbols_from_query("__partial")
        assert "__partial" not in result

    def test_underscore_prefix_no_internal_underscore(self) -> None:
        """_private (underscore prefix, no internal underscore) should be extracted.

        Regression test for #48: the snake_case pattern required at least one
        internal underscore, so _fetch_all worked but _private did not.
        """
        result = extract_symbols_from_query("_private method")
        assert "_private" in result, f"_private missing from {result}"

    def test_underscore_prefix_short_identifier_skipped(self) -> None:
        """_x (single char after underscore) is too short — should not be extracted."""
        result = extract_symbols_from_query("_x _y")
        assert "_x" not in result
        assert "_y" not in result


class TestPathRelevanceScoring:
    """Regression tests for upstream search ranking drift (#720)."""

    def test_pascal_case_word_counts_once_per_path_level(self) -> None:
        from pycodegraph.search.query_utils import score_path_relevance

        assert (
            score_path_relevance("SuperBizAgentFrontend/app.js", "SuperBizAgent") == 5
        )

    def test_pascal_case_word_still_matches_snake_case_path(self) -> None:
        from pycodegraph.search.query_utils import score_path_relevance

        assert score_path_relevance("get_user_name.go", "getUserName") >= 10

    def test_distinct_query_words_still_stack_scores(self) -> None:
        from pycodegraph.search.query_utils import score_path_relevance

        both = score_path_relevance("src/auth/login_handler.go", "auth handler")
        auth_only = score_path_relevance("src/auth/login_handler.go", "auth")
        assert both > auth_only

    def test_project_name_word_is_dropped_when_other_terms_remain(
        self, tmp_path
    ) -> None:
        from pycodegraph.search.query_utils import (
            derive_project_name_tokens,
            score_path_relevance,
        )

        root = tmp_path / "superbizagent"
        root.mkdir()
        (root / "pyproject.toml").write_text('[project]\nname = "superbizagent"\n')

        tokens = derive_project_name_tokens(str(root))
        with_drop = score_path_relevance(
            "SuperBizAgentFrontend/app.js", "SuperBizAgent backend", tokens
        )
        no_drop = score_path_relevance(
            "SuperBizAgentFrontend/app.js", "SuperBizAgent backend"
        )

        assert with_drop < no_drop
        assert with_drop == 0

    def test_project_name_word_is_kept_for_bare_query(self, tmp_path) -> None:
        from pycodegraph.search.query_utils import (
            derive_project_name_tokens,
            score_path_relevance,
        )

        root = tmp_path / "superbizagent"
        root.mkdir()
        (root / "pyproject.toml").write_text('[project]\nname = "superbizagent"\n')

        tokens = derive_project_name_tokens(str(root))
        assert (
            score_path_relevance(
                "SuperBizAgentFrontend/app.js", "SuperBizAgent", tokens
            )
            == 5
        )


class TestIsTestQuery:
    """Unit tests for the is_test_query heuristic."""

    def test_plain_test_keyword(self) -> None:
        assert is_test_query("test the payment flow")

    def test_spec_keyword(self) -> None:
        assert is_test_query("find the auth spec")

    def test_both_keywords(self) -> None:
        assert is_test_query("test spec for user model")

    def test_case_insensitive(self) -> None:
        assert is_test_query("Test the handler")
        assert is_test_query("SPEC for routing")

    def test_no_test_keyword(self) -> None:
        assert not is_test_query("how does authentication work")

    def test_substring_match_is_intentional(self) -> None:
        """Substring match is the current design — 'latest' triggers 'test'."""
        assert is_test_query("find the latest changes")
