"""Language extractors registry."""

from __future__ import annotations

from .base import LanguageExtractor
from .python import PYTHON_EXTRACTOR
from .typescript import TYPESCRIPT_EXTRACTOR, JAVASCRIPT_EXTRACTOR, JSX_EXTRACTOR, TSX_EXTRACTOR
from .go import GO_EXTRACTOR
from .java import JAVA_EXTRACTOR
from .rust import RUST_EXTRACTOR

from ...types import Language

EXTRACTORS: dict[Language, LanguageExtractor] = {
    Language.PYTHON: PYTHON_EXTRACTOR,
    Language.TYPESCRIPT: TYPESCRIPT_EXTRACTOR,
    Language.TSX: TSX_EXTRACTOR,
    Language.JAVASCRIPT: JAVASCRIPT_EXTRACTOR,
    Language.JSX: JSX_EXTRACTOR,
    Language.GO: GO_EXTRACTOR,
    Language.JAVA: JAVA_EXTRACTOR,
    Language.RUST: RUST_EXTRACTOR,
}
