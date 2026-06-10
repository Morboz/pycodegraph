"""Import path resolution and import mapping extraction."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..types import Node, UnresolvedReference
from ._types import ImportMapping, ResolvedRef

if TYPE_CHECKING:
    from ._context import ResolutionContext

# Extension resolution order by language
EXTENSION_RESOLUTION: dict[str, list[str]] = {
    "typescript": [
        ".ts",
        ".tsx",
        ".d.ts",
        ".js",
        ".jsx",
        "/index.ts",
        "/index.tsx",
        "/index.js",
    ],
    "javascript": [".js", ".jsx", ".mjs", ".cjs", "/index.js", "/index.jsx"],
    "tsx": [
        ".tsx",
        ".ts",
        ".d.ts",
        ".js",
        ".jsx",
        "/index.tsx",
        "/index.ts",
        "/index.js",
    ],
    "jsx": [".jsx", ".js", "/index.jsx", "/index.js"],
    "python": [".py", "/__init__.py"],
    "go": [".go"],
    "rust": [".rs", "/mod.rs"],
    "java": [".java"],
    "csharp": [".cs"],
}

# Python stdlib top-level module names for external import detection
_PYTHON_STDLIB_TOP = frozenset(
    {
        "os",
        "sys",
        "json",
        "re",
        "math",
        "datetime",
        "collections",
        "typing",
        "pathlib",
        "logging",
        "io",
        "abc",
        "argparse",
        "ast",
        "asyncio",
        "base64",
        "binascii",
        "bisect",
        "builtins",
        "calendar",
        "cgi",
        "cmath",
        "codecs",
        "configparser",
        "contextlib",
        "copy",
        "csv",
        "ctypes",
        "dataclasses",
        "decimal",
        "difflib",
        "dis",
        "email",
        "enum",
        "errno",
        "faulthandler",
        "fileinput",
        "fnmatch",
        "fractions",
        "ftplib",
        "functools",
        "gc",
        "getopt",
        "getpass",
        "glob",
        "graphlib",
        "gzip",
        "hashlib",
        "heapq",
        "hmac",
        "html",
        "http",
        "importlib",
        "inspect",
        "ipaddress",
        "itertools",
        "keyword",
        "linecache",
        "locale",
        "lzma",
        "mailbox",
        "marshal",
        "mimetypes",
        "mmap",
        "multiprocessing",
        "numbers",
        "operator",
        "optparse",
        "pdb",
        "pickle",
        "platform",
        "pprint",
        "profile",
        "pstats",
        "queue",
        "random",
        "reprlib",
        "runpy",
        "sched",
        "secrets",
        "select",
        "shelve",
        "shlex",
        "shutil",
        "signal",
        "socket",
        "sqlite3",
        "ssl",
        "stat",
        "statistics",
        "string",
        "struct",
        "subprocess",
        "symtable",
        "tarfile",
        "tempfile",
        "textwrap",
        "threading",
        "time",
        "timeit",
        "token",
        "tokenize",
        "traceback",
        "types",
        "unicodedata",
        "unittest",
        "urllib",
        "uuid",
        "venv",
        "warnings",
        "weakref",
        "xml",
        "zipfile",
        "zlib",
        "zoneinfo",
    }
)


def resolve_via_import(
    ref: UnresolvedReference,
    context: ResolutionContext,
) -> ResolvedRef | None:
    """Resolve a reference by matching it against the file's import declarations."""
    imports = context.get_import_mappings(ref.file_path, ref.language)
    if not imports and not context.read_file(ref.file_path):
        return None

    for imp in imports:
        if imp.local_name == ref.reference_name or ref.reference_name.startswith(
            imp.local_name + "."
        ):
            resolved_path = resolve_import_path(
                imp.source, ref.file_path, ref.language, context
            )
            if resolved_path:
                member_name = None
                if imp.is_namespace and ref.reference_name.startswith(
                    imp.local_name + "."
                ):
                    member_name = ref.reference_name[len(imp.local_name) + 1 :]

                target_node = _find_exported_symbol(
                    resolved_path,
                    imp.exported_name,
                    imp.is_default,
                    imp.is_namespace,
                    member_name,
                    ref.language,
                    context,
                )
                if target_node:
                    return ResolvedRef(
                        original=ref,
                        target_node_id=target_node.id,
                        confidence=0.9,
                        resolved_by="import",
                    )
    return None


def _find_exported_symbol(
    file_path: str,
    exported_name: str,
    is_default: bool,
    is_namespace: bool,
    member_name: str | None,
    language: str,
    context: ResolutionContext,
) -> Node | None:
    """Find an exported symbol in a file."""
    from ..types import NodeKind

    nodes_in_file = context.get_nodes_in_file(file_path)

    # In Python, all top-level definitions are implicitly exported
    # (there is no formal export system like JS/TS).
    require_exported = language != "python"

    if is_default:
        for n in nodes_in_file:
            if (not require_exported or n.is_exported) and n.kind in (
                NodeKind.FUNCTION,
                NodeKind.CLASS,
            ):
                return n
    elif is_namespace and member_name:
        for n in nodes_in_file:
            if n.name == member_name and (not require_exported or n.is_exported):
                return n
    else:
        for n in nodes_in_file:
            if n.name == exported_name and (not require_exported or n.is_exported):
                return n

    return None


def resolve_import_path(
    import_path: str,
    from_file: str,
    language: str,
    context: ResolutionContext,
) -> str | None:
    """Resolve an import path to a project-relative file path."""
    if _is_external_import(import_path, language):
        return None

    project_root = context.get_project_root()
    from_dir = str(Path(project_root) / Path(from_file).parent)

    # Relative imports
    if import_path.startswith("."):
        return _resolve_relative_import(import_path, from_dir, language, context)

    # Aliased/absolute imports (e.g., @/ or src/)
    return _resolve_aliased_import(import_path, project_root, language, context)


def _is_external_import(import_path: str, language: str) -> bool:
    """Check if an import refers to an external package."""
    if import_path.startswith("."):
        return False

    if language in ("typescript", "javascript", "tsx", "jsx"):
        node_builtins = {
            "fs",
            "path",
            "os",
            "crypto",
            "http",
            "https",
            "url",
            "util",
            "events",
            "stream",
            "child_process",
            "buffer",
        }
        if import_path in node_builtins:
            return True
        if (
            not import_path.startswith("@/")
            and not import_path.startswith("~/")
            and not import_path.startswith("src/")
        ):
            return True

    if language == "python":
        top_level = import_path.split(".")[0]
        if top_level in _PYTHON_STDLIB_TOP:
            return True

    return bool(
        language == "go"
        and not import_path.startswith(".")
        and "/internal/" not in import_path
    )


def _resolve_relative_import(
    import_path: str,
    from_dir: str,
    language: str,
    context: ResolutionContext,
) -> str | None:
    project_root = context.get_project_root()
    extensions = EXTENSION_RESOLUTION.get(language, [])

    base_path = os.path.normpath(os.path.join(from_dir, import_path))
    rel_path = os.path.relpath(base_path, project_root).replace("\\", "/")

    for ext in extensions:
        candidate = rel_path + ext
        if context.file_exists(candidate):
            return candidate

    if context.file_exists(rel_path):
        return rel_path

    return None


def _resolve_aliased_import(
    import_path: str,
    project_root: str,
    language: str,
    context: ResolutionContext,
) -> str | None:
    extensions = EXTENSION_RESOLUTION.get(language, [])

    def try_with_ext(base: str) -> str | None:
        for ext in extensions:
            candidate = base + ext
            if context.file_exists(candidate):
                return candidate
        if context.file_exists(base):
            return base
        return None

    fallback_aliases = {
        "@/": "src/",
        "~/": "src/",
        "@src/": "src/",
        "src/": "src/",
    }
    for alias, replacement in fallback_aliases.items():
        if import_path.startswith(alias):
            hit = try_with_ext(import_path.replace(alias, replacement, 1))
            if hit:
                return hit

    # Python absolute imports: convert dots to path separators
    # e.g. "myapp.models" → "myapp/models", then try .py and /__init__.py
    if language == "python" and "." in import_path:
        hit = find_python_module_file(import_path, context)
        if hit:
            return hit

    return try_with_ext(import_path)


def find_python_module_file(
    module_path: str,
    context: ResolutionContext,
) -> str | None:
    """Resolve a Python dotted module path to a project-relative file path.

    Converts ``a.b.c`` to ``a/b/c`` and tries:
      1. ``a/b/c.py``  (module file)
      2. ``a/b/c/__init__.py``  (package)

    Uses FILE nodes in the graph for matching (suffix-based), so this
    works even when the index only tracks a subset of the filesystem.
    """
    from ..types import NodeKind

    if not module_path or module_path.startswith("."):
        return None

    rel = module_path.replace(".", "/")  # a.b.c → a/b/c

    # Strategy 1: Try exact file_exists with converted path + extensions
    extensions = EXTENSION_RESOLUTION.get("python", [])
    for ext in extensions:
        candidate = rel + ext
        if context.file_exists(candidate):
            return candidate

    # Strategy 2: Search FILE nodes by suffix match
    # Look for .py files whose path ends with the converted module path
    last_seg = module_path.split(".")[-1]
    py_candidates = context.get_nodes_by_name(last_seg + ".py")
    for node in py_candidates:
        if node.kind == NodeKind.FILE and node.file_path.endswith(rel + ".py"):
            return node.file_path

    # Try package __init__.py
    init_candidates = context.get_nodes_by_name("__init__.py")
    for node in init_candidates:
        if node.kind == NodeKind.FILE and node.file_path.endswith(rel + "/__init__.py"):
            return node.file_path

    return None


def resolve_python_module_member(
    ref: UnresolvedReference,
    context: ResolutionContext,
) -> ResolvedRef | None:
    """Resolve ``receiver.member`` references where *receiver* is an imported module.

    When Python code does ``import utils; utils.helper()``, the reference
    name is ``utils.helper``.  This function finds the import for ``utils``,
    resolves it to a file, and looks up *helper* as a top-level definition
    in that file.
    """
    from ..types import NodeKind

    dot_idx = ref.reference_name.find(".")
    if dot_idx <= 0:
        return None

    receiver = ref.reference_name[:dot_idx]
    member = ref.reference_name[dot_idx + 1 :].split(".")[0]

    imports = context.get_import_mappings(ref.file_path, ref.language)
    for imp in imports:
        if imp.local_name != receiver:
            continue

        # Build the full module path to resolve
        if imp.is_namespace:
            module_path = imp.source
        else:
            module_path = imp.source + "." + imp.local_name

        resolved_path = resolve_import_path(
            module_path, ref.file_path, ref.language, context
        )
        if not resolved_path:
            resolved_path = find_python_module_file(module_path, context)
        if not resolved_path or resolved_path == ref.file_path:
            continue

        # Look for the member as a top-level definition in the resolved file
        target_kinds = (
            NodeKind.FUNCTION,
            NodeKind.CLASS,
            NodeKind.VARIABLE,
            NodeKind.CONSTANT,
        )
        for node in context.get_nodes_in_file(resolved_path):
            if node.name == member and node.kind in target_kinds:
                return ResolvedRef(
                    original=ref,
                    target_node_id=node.id,
                    confidence=0.85,
                    resolved_by="import",
                )

    return None


# --- Import mapping extraction ---


def extract_import_mappings(
    file_path: str, content: str, language: str
) -> list[ImportMapping]:
    mappings: list[ImportMapping] = []

    if language in ("typescript", "javascript", "tsx", "jsx"):
        mappings = _extract_js_imports(content)
    elif language == "python":
        mappings = _extract_python_imports(content)
    elif language == "go":
        mappings = _extract_go_imports(content)

    return mappings


def _extract_python_imports(content: str) -> list[ImportMapping]:
    mappings: list[ImportMapping] = []

    # from X import Y [as Z]
    for m in re.finditer(r"from\s+([\w.]+)\s+import\s+([^#\n]+)", content):
        source = m.group(1)
        imports_str = m.group(2)
        for name_str in imports_str.split(","):
            name_str = name_str.strip()
            if not name_str or name_str == "*":
                continue
            alias_match = re.match(r"(\w+)\s+as\s+(\w+)", name_str)
            if alias_match:
                mappings.append(
                    ImportMapping(
                        local_name=alias_match.group(2),
                        exported_name=alias_match.group(1),
                        source=source,
                    )
                )
            else:
                clean = name_str.strip("() ")
                if clean and re.match(r"^\w+$", clean):
                    mappings.append(
                        ImportMapping(
                            local_name=clean,
                            exported_name=clean,
                            source=source,
                        )
                    )

    # import X [as Y]
    for m in re.finditer(
        r"^import\s+([\w.]+)(?:\s+as\s+(\w+))?", content, re.MULTILINE
    ):
        source = m.group(1)
        alias = m.group(2)
        local_name = alias or source.split(".")[-1]
        mappings.append(
            ImportMapping(
                local_name=local_name,
                exported_name="*",
                source=source,
                is_namespace=True,
            )
        )

    return mappings


def _extract_js_imports(content: str) -> list[ImportMapping]:
    mappings: list[ImportMapping] = []

    # ES6: import [default] { named } from 'source'
    for m in re.finditer(
        r"import\s+(?:(\w+)\s*,?\s*)?(?:\{([^}]+)\})?\s*(?:(\*)\s+as\s+(\w+))?\s*from\s*['\"]([^'\"]+)['\"]",
        content,
    ):
        default_import = m.group(1)
        named_imports = m.group(2)
        namespace_alias = m.group(4)
        source = m.group(5)

        if default_import:
            mappings.append(
                ImportMapping(
                    local_name=default_import,
                    exported_name="default",
                    source=source,
                    is_default=True,
                )
            )

        if named_imports:
            for name_str in named_imports.split(","):
                name_str = name_str.strip()
                if not name_str:
                    continue
                alias_match = re.match(r"(\w+)\s+as\s+(\w+)", name_str)
                if alias_match:
                    mappings.append(
                        ImportMapping(
                            local_name=alias_match.group(2),
                            exported_name=alias_match.group(1),
                            source=source,
                        )
                    )
                else:
                    mappings.append(
                        ImportMapping(
                            local_name=name_str,
                            exported_name=name_str,
                            source=source,
                        )
                    )

        if namespace_alias:
            mappings.append(
                ImportMapping(
                    local_name=namespace_alias,
                    exported_name="*",
                    source=source,
                    is_namespace=True,
                )
            )

    # require()
    for m in re.finditer(
        r"(?:const|let|var)\s+(?:(\w+)|{([^}]+)})\s*=\s*require\(['\"]([^'\"]+)['\"]\)",
        content,
    ):
        default_name = m.group(1)
        destructured = m.group(2)
        source = m.group(3)

        if default_name:
            mappings.append(
                ImportMapping(
                    local_name=default_name,
                    exported_name="default",
                    source=source,
                    is_default=True,
                )
            )
        if destructured:
            for name_str in destructured.split(","):
                name_str = name_str.strip()
                if not name_str:
                    continue
                alias_match = re.match(r"(\w+)\s*:\s*(\w+)", name_str)
                if alias_match:
                    mappings.append(
                        ImportMapping(
                            local_name=alias_match.group(2),
                            exported_name=alias_match.group(1),
                            source=source,
                        )
                    )
                else:
                    mappings.append(
                        ImportMapping(
                            local_name=name_str,
                            exported_name=name_str,
                            source=source,
                        )
                    )

    return mappings


def _extract_go_imports(content: str) -> list[ImportMapping]:
    mappings: list[ImportMapping] = []

    # Single import
    for m in re.finditer(r'import\s+(?:(\w+)\s+)?["\']([^"\']+)["\']', content):
        alias = m.group(1)
        source = m.group(2)
        package_name = source.split("/")[-1]
        mappings.append(
            ImportMapping(
                local_name=alias or package_name,
                exported_name="*",
                source=source,
                is_namespace=True,
            )
        )

    # Import block
    block_match = re.search(r"import\s*\(\s*(.*?)\s*\)", content, re.DOTALL)
    if block_match:
        for m in re.finditer(r'(?:(\w+)\s+)?["\']([^"\']+)["\']', block_match.group(1)):
            alias = m.group(1)
            source = m.group(2)
            package_name = source.split("/")[-1]
            mappings.append(
                ImportMapping(
                    local_name=alias or package_name,
                    exported_name="*",
                    source=source,
                    is_namespace=True,
                )
            )

    return mappings
