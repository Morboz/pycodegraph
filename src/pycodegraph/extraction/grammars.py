"""Language detection and grammar management."""

from __future__ import annotations

from pathlib import Path

from ..types import Language

# File extension -> Language mapping
EXTENSION_MAP: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".pyw": Language.PYTHON,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TSX,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JSX,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".go": Language.GO,
    ".rs": Language.RUST,
    ".java": Language.JAVA,
    ".c": Language.C,
    ".h": Language.C,
    ".cpp": Language.CPP,
    ".cc": Language.CPP,
    ".cxx": Language.CPP,
    ".hpp": Language.CPP,
    ".cs": Language.CSHARP,
    ".php": Language.PHP,
    ".rb": Language.RUBY,
    ".swift": Language.SWIFT,
    ".kt": Language.KOTLIN,
    ".kts": Language.KOTLIN,
    ".dart": Language.DART,
    ".scala": Language.UNKNOWN,
    ".sc": Language.UNKNOWN,
}

# Language -> tree-sitter language name (for tree_sitter.Language)
LANG_TO_TS_NAME: dict[Language, str] = {
    Language.PYTHON: "python",
    Language.TYPESCRIPT: "typescript",
    Language.TSX: "tsx",
    Language.JAVASCRIPT: "javascript",
    Language.JSX: "javascript",
    Language.GO: "go",
    Language.RUST: "rust",
    Language.JAVA: "java",
    Language.C: "c",
    Language.CPP: "cpp",
    Language.CSHARP: "csharp",
    Language.PHP: "php",
    Language.RUBY: "ruby",
    Language.SWIFT: "swift",
    Language.KOTLIN: "kotlin",
    Language.DART: "dart",
}

# Cache of loaded languages
_language_cache: dict[Language, object] = {}


def detect_language(file_path: str) -> Language:
    """Detect language from file extension."""
    ext = Path(file_path).suffix.lower()
    return EXTENSION_MAP.get(ext, Language.UNKNOWN)


def is_language_supported(language: Language) -> bool:
    """Check if a language has a tree-sitter grammar available."""
    return language in LANG_TO_TS_NAME


def get_language(language: Language):
    """Get or load a tree-sitter Language object."""
    from tree_sitter import Language as TSLanguage

    if language in _language_cache:
        return _language_cache[language]

    ts_name = LANG_TO_TS_NAME.get(language)
    if not ts_name:
        return None

    def _wrap(raw):
        if isinstance(raw, TSLanguage):
            return raw
        return TSLanguage(raw)

    # Map language enum to (module_name, getter_name)
    _lang_modules = {
        Language.PYTHON: ("tree_sitter_python", "language"),
        Language.TYPESCRIPT: ("tree_sitter_typescript", "language_typescript"),
        Language.TSX: ("tree_sitter_typescript", "language_tsx"),
        Language.JAVASCRIPT: ("tree_sitter_javascript", "language"),
        Language.JSX: ("tree_sitter_javascript", "language"),
        Language.GO: ("tree_sitter_go", "language"),
        Language.RUST: ("tree_sitter_rust", "language"),
        Language.JAVA: ("tree_sitter_java", "language"),
        Language.C: ("tree_sitter_c", "language"),
        Language.CPP: ("tree_sitter_cpp", "language"),
        Language.CSHARP: ("tree_sitter_c_sharp", "language"),
        Language.PHP: ("tree_sitter_php", "language"),
        Language.RUBY: ("tree_sitter_ruby", "language"),
        Language.SWIFT: ("tree_sitter_swift", "language"),
        Language.KOTLIN: ("tree_sitter_kotlin", "language"),
        Language.DART: ("tree_sitter_dart", "language"),
    }

    entry = _lang_modules.get(language)
    if not entry:
        return None

    module_name, getter_name = entry
    try:
        mod = __import__(module_name)
    except ImportError:
        return None

    raw_lang = getattr(mod, getter_name)()
    lang = _wrap(raw_lang)
    _language_cache[language] = lang
    return lang


def get_parser(language: Language):
    """Get a configured tree-sitter Parser for the given language."""
    from tree_sitter import Parser

    lang = get_language(language)
    if not lang:
        return None

    parser = Parser(lang)
    return parser
