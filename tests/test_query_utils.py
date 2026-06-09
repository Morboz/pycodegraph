"""Tests for extract_symbols_from_query — symbol extraction from NL queries."""

from pycodegraph.search.query_utils import extract_symbols_from_query


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
