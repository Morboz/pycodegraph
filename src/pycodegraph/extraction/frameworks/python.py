"""Python framework extractors — Flask, FastAPI, Django.

Each extractor can:
  1. ``detect()`` — check if its framework is used in the project.
  2. ``extract()`` — scan a Python source file for ROUTE nodes and handler refs.

Route extraction is regex-based (matching the reference TypeScript
implementation) and runs on comment-stripped source for reliability.
Resolution of the handler references is left to the standard name-based
resolver — framework extractors only produce the ROUTE nodes and
REFERENCES/IMPORTS refs.
"""

from __future__ import annotations

import re
import time

from ...types import EdgeKind, Language, Node, NodeKind, UnresolvedReference
from . import FileReader, FrameworkExtractionResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Pattern that matches Python comments which could confuse
# route-detection regexes.  We replace them with spaces so line
# numbers stay the same.
_COMMENT_RE = re.compile(r"#.*$", re.MULTILINE)


def _strip_comments(content: str) -> str:
    """Remove comments from Python source, preserving line structure.

    Unlike the TypeScript reference which also strips string literals,
    we keep strings intact because our route-extraction regexes need to
    match quoted path arguments like '/users'.  We only strip ``#``-style
    comments to prevent false matches inside comment blocks.
    """
    return _COMMENT_RE.sub(" ", content)


def _line_at(text: str, offset: int) -> int:
    """1-based line number at *offset* in *text*."""
    return text[:offset].count("\n") + 1


def _make_route_node(
    *,
    file_path: str,
    line: int,
    name: str,
    qualified_name: str,
    end_column: int = 0,
) -> Node:
    now = int(time.time() * 1000)
    return Node(
        id=f"route:{file_path}:{line}:{name}",
        kind=NodeKind.ROUTE,
        name=name,
        qualified_name=qualified_name,
        file_path=file_path,
        language=Language.PYTHON,
        start_line=line,
        end_line=line,
        start_column=0,
        end_column=end_column,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------

# @app.route('/path', methods=[...])
# @users_bp.route('/path', methods=(...))
_FLASK_DECORATOR_RE = re.compile(
    r"@(\w+)\.route\s*\(\s*['\"]([^'\"]*)['\"]"
    r"(?:\s*,\s*methods\s*=\s*[\[({]([^\]\})]+)[\])}])?\s*\)"
)

# Flask-RESTful: api.add_resource(ResourceClass, '/path')
_FLASK_RESTFUL_RE = re.compile(
    r"\.add\w*[Rr]esource\s*\(\s*(\w+)\s*,\s*((?:['\"][^'\"]+['\"]\s*,?\s*)+)"
)


class FlaskExtractor:
    """Flask and Flask-RESTful route extractor."""

    name = "flask"

    def detect(self, file_reader: FileReader) -> bool:
        for fname in ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py"):
            content = file_reader.read_file(fname)
            if content and re.search(r"\bflask\b", content, re.IGNORECASE):
                return True

        # Scan entrypoint-named files for Flask instantiation
        entrypoint_re = re.compile(
            r"(?:^|/)(?:app|application|main|wsgi|__init__)\.py$"
        )
        for fpath in file_reader.list_files():
            if entrypoint_re.search(fpath):
                content = file_reader.read_file(fpath)
                if (
                    content
                    and re.search(r"\bFlask\s*\(", content)
                    and re.search(r"\bimport\s+flask\b|\bfrom\s+flask\b", content)
                ):
                    return True
        return False

    def extract(self, file_path: str, content: str) -> FrameworkExtractionResult:
        if not file_path.endswith(".py"):
            return FrameworkExtractionResult()

        safe = _strip_comments(content)
        nodes: list[Node] = []
        refs: list[UnresolvedReference] = []

        # --- Decorator routes ---
        for m in _FLASK_DECORATOR_RE.finditer(safe):
            _app_name = m.group(1)
            route_path = m.group(2)
            methods_group = m.group(3)

            method = "GET"  # Flask default
            if methods_group:
                mm = re.search(r"['\"]([A-Z]+)['\"]", methods_group, re.IGNORECASE)
                if mm:
                    method = mm.group(1).upper()

            line = _line_at(safe, m.start())
            name = f"{method} {route_path or '/'}"
            route_node = _make_route_node(
                file_path=file_path,
                line=line,
                name=name,
                qualified_name=f"{file_path}::{method}:{route_path}",
                end_column=m.end() - m.start(),
            )
            nodes.append(route_node)

            # Find the handler def on the next line(s)
            handler_name = self._find_handler(safe, m.end())
            if handler_name:
                refs.append(
                    UnresolvedReference(
                        from_node_id=route_node.id,
                        reference_name=handler_name,
                        reference_kind=EdgeKind.REFERENCES,
                        line=line,
                        column=0,
                        file_path=file_path,
                        language="python",
                    )
                )

        # --- Flask-RESTful ---
        for m in _FLASK_RESTFUL_RE.finditer(safe):
            class_name = m.group(1)
            paths = re.findall(r"['\"]([^'\"]+)['\"]", m.group(2))
            line = _line_at(safe, m.start())
            for route_path in paths:
                route_name = f"ANY {route_path}"
                route_node = _make_route_node(
                    file_path=file_path,
                    line=line,
                    name=route_name,
                    qualified_name=f"{file_path}::ANY:{route_path}",
                )
                nodes.append(route_node)
                refs.append(
                    UnresolvedReference(
                        from_node_id=route_node.id,
                        reference_name=class_name,
                        reference_kind=EdgeKind.REFERENCES,
                        line=line,
                        column=0,
                        file_path=file_path,
                        language="python",
                    )
                )

        return FrameworkExtractionResult(nodes=nodes, references=refs)

    @staticmethod
    def _find_handler(content: str, after: int) -> str | None:
        """Look for ``def <name>`` after *after* offset."""
        tail = content[after:]
        m = re.search(r"\n\s*(?:async\s+)?def\s+(\w+)", tail)
        return m.group(1) if m else None


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

# @app.get('/path'), @router.post('/path'), etc.
_FASTAPI_DECORATOR_RE = re.compile(
    r"@(\w+)\.(get|post|put|patch|delete|options|head)\s*\(\s*['\"]([^'\"]*)['\"]"
)


class FastAPIExtractor:
    """FastAPI route extractor."""

    name = "fastapi"

    def detect(self, file_reader: FileReader) -> bool:
        for fname in ("requirements.txt", "pyproject.toml"):
            content = file_reader.read_file(fname)
            if content and re.search(r"\bfastapi\b", content, re.IGNORECASE):
                return True
        # Check entry files for FastAPI(
        for fname in ("app.py", "main.py", "api.py"):
            content = file_reader.read_file(fname)
            if content and "FastAPI(" in content:
                return True
        return False

    def extract(self, file_path: str, content: str) -> FrameworkExtractionResult:
        if not file_path.endswith(".py"):
            return FrameworkExtractionResult()

        safe = _strip_comments(content)
        nodes: list[Node] = []
        refs: list[UnresolvedReference] = []

        for m in _FASTAPI_DECORATOR_RE.finditer(safe):
            http_method = m.group(2).upper()
            route_path = m.group(3)
            line = _line_at(safe, m.start())
            name = f"{http_method} {route_path or '/'}"
            route_node = _make_route_node(
                file_path=file_path,
                line=line,
                name=name,
                qualified_name=f"{file_path}::{http_method}:{route_path}",
                end_column=m.end() - m.start(),
            )
            nodes.append(route_node)

            handler_name = FlaskExtractor._find_handler(safe, m.end())
            if handler_name:
                refs.append(
                    UnresolvedReference(
                        from_node_id=route_node.id,
                        reference_name=handler_name,
                        reference_kind=EdgeKind.REFERENCES,
                        line=line,
                        column=0,
                        file_path=file_path,
                        language="python",
                    )
                )

        return FrameworkExtractionResult(nodes=nodes, references=refs)


# ---------------------------------------------------------------------------
# Django
# ---------------------------------------------------------------------------

# path('url', handler, name=...) / re_path(r'...', handler) / url(r'...', handler)
_DJANGO_ROUTE_RE = re.compile(
    r"\b(path|re_path|url)\s*\(\s*r?['\"]([^'\"]+)['\"]\s*,\s*([\w.]+(?:\s*\([^)]*\))?)"
)

# DRF router.register(r'prefix', ViewSet)
_DJANGO_ROUTER_RE = re.compile(
    r"\.register\s*\(\s*r?['\"]([^'\"]+)['\"]\s*,\s*([\w.]+)"
)


def _resolve_django_handler(expr: str) -> tuple[str, EdgeKind] | None:
    """Parse a Django URL handler expression and return (symbol, kind)."""
    # include('module.path')
    m = re.match(r"^include\s*\(\s*['\"]([^'\"]+)['\"]", expr)
    if m:
        return m.group(1), EdgeKind.IMPORTS

    # Strip .as_view(...) or .as_view()
    head = re.sub(r"\.as_view\s*\([^)]*\)\s*$", "", expr)
    # Drop any other trailing method call
    head = re.sub(r"\.\w+\s*\([^)]*\)\s*$", "", head)

    dotted = [p for p in head.split(".") if p]
    if not dotted:
        return None
    last = dotted[-1]
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", last):
        return None
    return last, EdgeKind.REFERENCES


class DjangoExtractor:
    """Django and Django REST Framework route extractor."""

    name = "django"

    def detect(self, file_reader: FileReader) -> bool:
        for fname in ("requirements.txt", "setup.py", "pyproject.toml"):
            content = file_reader.read_file(fname)
            if content and "django" in content.lower():
                return True
        return file_reader.file_exists("manage.py")

    def extract(self, file_path: str, content: str) -> FrameworkExtractionResult:
        if not file_path.endswith(".py"):
            return FrameworkExtractionResult()

        safe = _strip_comments(content)
        nodes: list[Node] = []
        refs: list[UnresolvedReference] = []

        # --- path() / re_path() / url() ---
        for m in _DJANGO_ROUTE_RE.finditer(safe):
            url_path = m.group(2)
            handler_expr = m.group(3).strip()
            line = _line_at(safe, m.start())

            route_node = _make_route_node(
                file_path=file_path,
                line=line,
                name=url_path,
                qualified_name=f"{file_path}::route:{url_path}",
                end_column=m.end() - m.start(),
            )
            nodes.append(route_node)

            resolved = _resolve_django_handler(handler_expr)
            if resolved:
                target_name, ref_kind = resolved
                refs.append(
                    UnresolvedReference(
                        from_node_id=route_node.id,
                        reference_name=target_name,
                        reference_kind=ref_kind,
                        line=line,
                        column=0,
                        file_path=file_path,
                        language="python",
                    )
                )

        # --- DRF router.register(r'prefix', ViewSet) ---
        for m in _DJANGO_ROUTER_RE.finditer(safe):
            prefix = m.group(1).lstrip("^").rstrip("/$")
            viewset = m.group(2).split(".").pop()
            # Only match View/ViewSet suffixes (avoid admin.site.register)
            if not re.search(r"View(Set)?$", viewset):
                continue
            line = _line_at(safe, m.start())
            route_name = f"VIEWSET /{prefix}"
            route_node = _make_route_node(
                file_path=file_path,
                line=line,
                name=route_name,
                qualified_name=f"{file_path}::route:{prefix}",
            )
            nodes.append(route_node)
            refs.append(
                UnresolvedReference(
                    from_node_id=route_node.id,
                    reference_name=viewset,
                    reference_kind=EdgeKind.REFERENCES,
                    line=line,
                    column=0,
                    file_path=file_path,
                    language="python",
                )
            )

        return FrameworkExtractionResult(nodes=nodes, references=refs)
