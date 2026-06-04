"""Search query utilities - term extraction and scoring."""

from __future__ import annotations

import os
import re

STOP_WORDS: frozenset[str] = frozenset(
    {
        # English
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "it",
        "that",
        "this",
        "are",
        "was",
        "be",
        "has",
        "had",
        "have",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "shall",
        "not",
        "no",
        "all",
        "each",
        "every",
        "how",
        "what",
        "where",
        "when",
        "who",
        "which",
        "why",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "he",
        "she",
        "they",
        "show",
        "give",
        "tell",
        "been",
        "done",
        "made",
        "used",
        "using",
        "work",
        "works",
        "found",
        "also",
        "into",
        "then",
        "than",
        "just",
        "more",
        "some",
        "such",
        "over",
        "only",
        "out",
        "its",
        "so",
        "up",
        "as",
        "if",
        "look",
        "need",
        "needs",
        "want",
        "happen",
        "happens",
        "affect",
        "affected",
        "break",
        "breaks",
        "failing",
        "implemented",
        "implement",
        # Code-specific noise
        "code",
        "file",
        "files",
        "function",
        "method",
        "class",
        "type",
        "fix",
        "bug",
        "called",
    }
)


def get_stem_variants(term: str) -> list[str]:
    """Generate stem variants by removing common English suffixes."""
    variants: set[str] = set()
    t = term.lower()

    # -ing
    if t.endswith("ing") and len(t) > 5:
        base = t[:-3]
        variants.add(base)
        variants.add(base + "e")
        if len(base) >= 2 and base[-1] == base[-2]:
            variants.add(base[:-1])

    # -tion/-sion
    if (t.endswith("tion") or t.endswith("sion")) and len(t) > 5:
        variants.add(t[:-3])

    # -ment
    if t.endswith("ment") and len(t) > 6:
        variants.add(t[:-4])

    # -ies
    if t.endswith("ies") and len(t) > 4:
        variants.add(t[:-3] + "y")
    elif t.endswith("es") and len(t) > 4:
        variants.add(t[:-2])
    elif t.endswith("s") and not t.endswith("ss") and len(t) > 4:
        variants.add(t[:-1])

    # -ed
    if t.endswith("ed") and not t.endswith("eed") and len(t) > 4:
        variants.add(t[:-1])
        variants.add(t[:-2])
        if t.endswith("ied") and len(t) > 5:
            variants.add(t[:-3] + "y")

    # -er
    if t.endswith("er") and len(t) > 4:
        base = t[:-2]
        variants.add(base)
        variants.add(base + "e")
        if len(base) >= 2 and base[-1] == base[-2]:
            variants.add(base[:-1])

    return [v for v in variants if len(v) >= 3 and v != t]


def extract_search_terms(query: str, stems: bool = True) -> list[str]:
    """Extract meaningful search terms from a natural language query."""
    tokens: set[str] = set()

    # CamelCase / PascalCase compound identifiers
    for m in re.finditer(
        r"\b([a-zA-Z][a-zA-Z0-9]*(?:[A-Z][a-z]+)+|[A-Z][a-z]+(?:[A-Z][a-z]*)+)\b",
        query,
    ):
        if len(m.group(1)) >= 3:
            tokens.add(m.group(1).lower())

    # snake_case
    for m in re.finditer(r"\b([a-zA-Z][a-zA-Z0-9]*(?:_[a-zA-Z0-9]+)+)\b", query):
        if len(m.group(1)) >= 3:
            tokens.add(m.group(1).lower())

    # Split camelCase, replace _/., split on non-alnum
    camel_split = re.sub(r"([a-z])([A-Z])", r"\1 \2", query)
    camel_split = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", camel_split)
    normalised = re.sub(r"[_.]+", " ", camel_split)
    words = [w for w in re.split(r"[^a-zA-Z0-9]+", normalised) if w]

    for word in words:
        lower = word.lower()
        if len(lower) < 3 or lower in STOP_WORDS:
            continue
        tokens.add(lower)

    if stems:
        stem_set: set[str] = set()
        for token in list(tokens):
            for variant in get_stem_variants(token):
                if variant not in tokens and variant not in STOP_WORDS:
                    stem_set.add(variant)
        tokens |= stem_set

    return list(tokens)


def score_path_relevance(file_path: str, query: str) -> float:
    """Score path relevance to a query. Higher = more relevant."""
    terms = extract_search_terms(query, stems=False)
    if not terms:
        return 0.0

    path_lower = file_path.lower()
    file_name = os.path.basename(path_lower)
    dir_name = os.path.dirname(path_lower)
    score = 0.0

    for term in terms:
        if term in file_name:
            score += 10
        elif term in dir_name:
            score += 5
        elif term in path_lower:
            score += 3

    query_lower = query.lower()
    is_test_query = "test" in query_lower or "spec" in query_lower
    if not is_test_query and is_test_file(file_path):
        score -= 15

    return score


_NON_PROD_DIRS = (
    "integration",
    "sample",
    "samples",
    "example",
    "examples",
    "fixture",
    "fixtures",
    "benchmark",
    "benchmarks",
    "demo",
    "demos",
)

_TEST_SUFFIXES = (
    ".test.ts",
    ".test.js",
    ".test.tsx",
    ".test.jsx",
    ".spec.ts",
    ".spec.js",
    "_test.go",
    "_test.py",
    "_test.rs",
    "Tests.java",
    "Test.java",
    "Tester.java",
    "TestCase.java",
)

_TEST_DIRS = (
    "/tests/",
    "/test/",
    "/__tests__/",
    "/spec/",
    "/testlib/",
    "/testing/",
)


def is_test_file(file_path: str) -> bool:
    """Check if a file path looks like a test file."""
    lower = file_path.lower()
    file_name = os.path.basename(lower)

    if file_name.startswith("test_") or file_name.startswith("test."):
        return True

    for suffix in _TEST_SUFFIXES:
        if file_name.endswith(suffix.lower()):
            return True

    for td in _TEST_DIRS:
        if td in lower:
            return True

    # Non-production dirs
    return any(f"/{d}/" in lower or lower.startswith(f"{d}/") for d in _NON_PROD_DIRS)


def name_match_bonus(node_name: str, query: str) -> float:
    """Bonus when a node's name matches the search query."""
    name_lower = node_name.lower()

    raw_terms = [
        t.lower()
        for t in re.split(r"[\s_.\-]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", query))
        if len(t) >= 2
    ]
    query_tokens = [t.lower() for t in query.split() if len(t) >= 2]
    query_lower = re.sub(r"\s+", "", query).lower()

    if name_lower == query_lower:
        return 80.0
    if len(query_tokens) > 1 and name_lower in query_tokens:
        return 60.0
    if name_lower.startswith(query_lower):
        ratio = len(query_lower) / len(name_lower)
        return round(10 + 30 * ratio)
    if len(raw_terms) > 1 and all(t in name_lower for t in raw_terms):
        return 15.0
    if query_lower in name_lower:
        return 10.0
    return 0.0


_KIND_BONUSES: dict[str, float] = {
    "function": 10,
    "method": 10,
    "class": 8,
    "interface": 9,
    "type_alias": 6,
    "struct": 6,
    "trait": 9,
    "enum": 5,
    "component": 8,
    "route": 9,
    "module": 4,
    "property": 3,
    "field": 3,
    "variable": 2,
    "constant": 3,
    "import": 1,
    "export": 1,
    "parameter": 0,
    "namespace": 4,
    "file": 0,
    "protocol": 9,
    "enum_member": 3,
}


def kind_bonus(kind: str) -> float:
    """Kind-based bonus for search ranking."""
    return _KIND_BONUSES.get(kind, 0.0)


# =============================================================================
# Symbol extraction from natural language queries
# =============================================================================


_SYMBOL_COMMON_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "have",
        "been",
        "will",
        "would",
        "could",
        "should",
        "does",
        "done",
        "make",
        "made",
        "use",
        "used",
        "using",
        "work",
        "works",
        "find",
        "found",
        "show",
        "call",
        "called",
        "calling",
        "get",
        "set",
        "add",
        "all",
        "any",
        "how",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "not",
        "but",
        "are",
        "was",
        "were",
        "has",
        "had",
        "its",
        "can",
        "did",
        "may",
        "also",
        "into",
        "than",
        "then",
        "them",
        "each",
        "other",
        "some",
        "such",
        "only",
        "same",
        "about",
        "after",
        "before",
        "between",
        "through",
        "during",
        "without",
        "again",
        "further",
        "once",
        "here",
        "there",
        "both",
        "just",
        "more",
        "most",
        "very",
        "being",
        "having",
        "doing",
        "system",
        "need",
        "needs",
        "want",
        "wants",
        "like",
        "look",
        "change",
        "changes",
        "changed",
        "changing",
        "layer",
        "handle",
        "handles",
        "handling",
        "incoming",
        "outgoing",
        "data",
        "flow",
        "flows",
        "level",
        "levels",
        "request",
        "requests",
        "response",
        "responses",
        "implement",
        "implements",
        "implementation",
        "interface",
        "interfaces",
        "class",
        "classes",
        "method",
        "methods",
        "trigger",
        "triggers",
        "affected",
        "affect",
        "affects",
        "else",
        "code",
        "failing",
        "failed",
        "silently",
        "decide",
        "decides",
        "return",
        "returns",
        "returned",
        "take",
        "takes",
        "taken",
        "check",
        "checks",
        "checked",
        "create",
        "creates",
        "created",
        "read",
        "reads",
        "write",
        "writes",
        "written",
        "start",
        "starts",
        "stop",
        "stops",
        "run",
        "runs",
        "running",
    }
)


def extract_symbols_from_query(query: str) -> list[str]:
    """Extract likely symbol names from a natural language query.

    Identifies potential code symbols using patterns:
    - CamelCase: UserService, signInWithGoogle
    - snake_case: user_service, sign_in
    - SCREAMING_SNAKE: MAX_RETRIES
    - dot.notation: app.isPackaged (extracts both sides)
    - Acronyms: REST, HTTP
    - Plain lowercase identifiers: undo, redo, history

    Filters out common English words that aren't likely symbol names.
    """
    symbols: set[str] = set()

    # CamelCase
    for m in re.finditer(
        r"\b([A-Z][a-z]+(?:[A-Z][a-z]*)*|[a-z]+(?:[A-Z][a-z]*)+)\b",
        query,
    ):
        if len(m.group(1)) >= 2:
            symbols.add(m.group(1))

    # snake_case
    for m in re.finditer(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", query, re.IGNORECASE):
        if len(m.group(1)) >= 3:
            symbols.add(m.group(1))

    # SCREAMING_SNAKE
    for m in re.finditer(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b", query):
        if m.group(1):
            symbols.add(m.group(1))

    # Acronyms
    for m in re.finditer(r"\b([A-Z]{2,})\b", query):
        if m.group(1):
            symbols.add(m.group(1))

    # dot.notation
    for m in re.finditer(
        r"\b([a-zA-Z][a-zA-Z0-9]*(?:\.[a-zA-Z][a-zA-Z0-9]*)+)\b", query
    ):
        parts = m.group(1).split(".")
        for part in parts:
            if len(part) >= 2:
                symbols.add(part)
        symbols.add(m.group(1))

    # Plain lowercase identifiers
    for m in re.finditer(r"\b([a-z][a-z0-9]{2,})\b", query):
        symbols.add(m.group(1))

    return sorted(
        (s for s in symbols if s.lower() not in _SYMBOL_COMMON_WORDS),
        key=lambda s: (-len(s), s),
    )
