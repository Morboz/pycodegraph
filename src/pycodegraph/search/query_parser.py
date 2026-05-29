"""Field-qualified search query parser.

Splits a raw query like ``kind:function name:auth path:src/api authenticate``
into structured filters plus free-text for FTS.
"""

from __future__ import annotations

from ..types import Language, NodeKind

# Runtime-iterable value sets for validation
KIND_VALUES: frozenset[str] = frozenset(k.value for k in NodeKind)
LANGUAGE_VALUES: frozenset[str] = frozenset(lang.value for lang in Language)


class ParsedQuery:
    __slots__ = ("kinds", "languages", "name_filters", "path_filters", "text")

    def __init__(self) -> None:
        self.text: str = ""
        self.kinds: list[str] = []
        self.languages: list[str] = []
        self.path_filters: list[str] = []
        self.name_filters: list[str] = []


def _unquote(s: str) -> str:
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


def _tokenise(raw: str) -> list[str]:
    """Tokenise on whitespace, preserving quoted spans."""
    tokens: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        while i < n and raw[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        while i < n and not raw[i].isspace():
            if raw[i] == '"':
                end = raw.find('"', i + 1)
                if end == -1:
                    i = n
                    break
                i = end + 1
                continue
            i += 1
        tokens.append(raw[start:i])
    return tokens


def parse_query(raw: str) -> ParsedQuery:
    """Parse a raw query into structured filters + remaining text."""
    out = ParsedQuery()
    tokens = _tokenise(raw)
    text_parts: list[str] = []

    for tok in tokens:
        colon = tok.find(":")
        if colon <= 0 or colon == len(tok) - 1:
            text_parts.append(tok)
            continue
        key = tok[:colon].lower()
        value_raw = _unquote(tok[colon + 1 :])
        if not value_raw:
            text_parts.append(tok)
            continue
        if key == "kind":
            if value_raw in KIND_VALUES:
                out.kinds.append(value_raw)
            else:
                text_parts.append(tok)
        elif key in ("lang", "language"):
            lower = value_raw.lower()
            if lower in LANGUAGE_VALUES:
                out.languages.append(lower)
            else:
                text_parts.append(tok)
        elif key == "path":
            out.path_filters.append(value_raw)
        elif key == "name":
            out.name_filters.append(value_raw)
        else:
            text_parts.append(tok)

    out.text = " ".join(text_parts).strip()
    return out


def bounded_edit_distance(a: str, b: str, max_dist: int) -> int:
    """Damerau-Levenshtein bounded edit distance with early exit.

    Returns ``max_dist + 1`` as soon as distance exceeds *max_dist*.
    """
    if a == b:
        return 0
    al, bl = len(a), len(b)
    if abs(al - bl) > max_dist:
        return max_dist + 1
    if al == 0:
        return bl
    if bl == 0:
        return al

    prev = list(range(bl + 1))
    cur = [0] * (bl + 1)

    for i in range(1, al + 1):
        cur[0] = i
        row_min = cur[0]
        for j in range(1, bl + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
            if cur[j] < row_min:
                row_min = cur[j]
        if row_min > max_dist:
            return max_dist + 1
        prev, cur = cur, prev

    return prev[bl]
