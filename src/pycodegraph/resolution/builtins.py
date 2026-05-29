"""Built-in and external symbol detection for reference resolution."""

from __future__ import annotations

import sys

from .types import UnresolvedRef

# --- Python built-ins ---

PYTHON_BUILTINS = frozenset(
    {
        "print",
        "len",
        "range",
        "str",
        "int",
        "float",
        "list",
        "dict",
        "set",
        "tuple",
        "bool",
        "bytes",
        "bytearray",
        "frozenset",
        "object",
        "complex",
        "open",
        "input",
        "type",
        "isinstance",
        "hasattr",
        "getattr",
        "setattr",
        "super",
        "self",
        "cls",
        "None",
        "True",
        "False",
        "next",
        "iter",
        "any",
        "all",
        "abs",
        "bin",
        "chr",
        "dir",
        "divmod",
        "enumerate",
        "eval",
        "exec",
        "filter",
        "format",
        "globals",
        "hex",
        "id",
        "map",
        "max",
        "min",
        "oct",
        "ord",
        "pow",
        "repr",
        "reversed",
        "round",
        "sorted",
        "sum",
        "vars",
        "zip",
        "property",
        "staticmethod",
        "classmethod",
        "abstractmethod",
        "NotImplemented",
        "Ellipsis",
        "__import__",
    }
)

PYTHON_BUILTIN_TYPES = frozenset(
    {
        "list",
        "dict",
        "set",
        "tuple",
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "bytearray",
        "frozenset",
        "object",
        "super",
    }
)

PYTHON_BUILTIN_METHODS = frozenset(
    {
        "append",
        "extend",
        "insert",
        "remove",
        "pop",
        "clear",
        "sort",
        "reverse",
        "copy",
        "update",
        "keys",
        "values",
        "items",
        "get",
        "add",
        "discard",
        "union",
        "intersection",
        "difference",
        "split",
        "join",
        "strip",
        "lstrip",
        "rstrip",
        "replace",
        "lower",
        "upper",
        "startswith",
        "endswith",
        "find",
        "index",
        "count",
        "encode",
        "decode",
        "format",
        "isdigit",
        "isalpha",
        "isalnum",
        "read",
        "write",
        "readline",
        "readlines",
        "close",
        "flush",
        "seek",
        "execute",
        "executemany",
        "fetchall",
        "fetchone",
        "fetchmany",
        "commit",
        "rollback",
        "cursor",
        "connect",
    }
)

# Python stdlib top-level modules (fallback for Python < 3.10)
_PYTHON_STDLIB_FALLBACK = frozenset(
    {
        "abc",
        "aifc",
        "argparse",
        "array",
        "ast",
        "asynchat",
        "asyncio",
        "asyncore",
        "atexit",
        "audioop",
        "base64",
        "bdb",
        "binascii",
        "binhex",
        "bisect",
        "builtins",
        "bz2",
        "calendar",
        "cgi",
        "cgitb",
        "chunk",
        "cmath",
        "cmd",
        "code",
        "codecs",
        "codeop",
        "collections",
        "colorsys",
        "compileall",
        "concurrent",
        "configparser",
        "contextlib",
        "contextvars",
        "copy",
        "copyreg",
        "cProfile",
        "crypt",
        "csv",
        "ctypes",
        "curses",
        "dataclasses",
        "datetime",
        "dbm",
        "decimal",
        "difflib",
        "dis",
        "distutils",
        "doctest",
        "email",
        "encodings",
        "enum",
        "errno",
        "faulthandler",
        "fcntl",
        "filecmp",
        "fileinput",
        "fnmatch",
        "fractions",
        "ftplib",
        "functools",
        "gc",
        "getopt",
        "getpass",
        "gettext",
        "glob",
        "graphlib",
        "grp",
        "gzip",
        "hashlib",
        "heapq",
        "hmac",
        "html",
        "http",
        "idlelib",
        "imaplib",
        "imghdr",
        "imp",
        "importlib",
        "inspect",
        "io",
        "ipaddress",
        "itertools",
        "json",
        "keyword",
        "lib2to3",
        "linecache",
        "locale",
        "logging",
        "lzma",
        "mailbox",
        "mailcap",
        "marshal",
        "math",
        "mimetypes",
        "mmap",
        "modulefinder",
        "multiprocessing",
        "netrc",
        "nis",
        "nntplib",
        "numbers",
        "operator",
        "optparse",
        "os",
        "ossaudiodev",
        "parser",
        "pathlib",
        "pdb",
        "pickle",
        "pickletools",
        "pipes",
        "pkgutil",
        "platform",
        "plistlib",
        "poplib",
        "posix",
        "posixpath",
        "pprint",
        "profile",
        "pstats",
        "pty",
        "pwd",
        "py_compile",
        "pyclbr",
        "pydoc",
        "queue",
        "quopri",
        "random",
        "re",
        "readline",
        "reprlib",
        "resource",
        "rlcompleter",
        "runpy",
        "sched",
        "secrets",
        "select",
        "selectors",
        "shelve",
        "shlex",
        "shutil",
        "signal",
        "site",
        "smtpd",
        "smtplib",
        "sndhdr",
        "socket",
        "socketserver",
        "spwd",
        "sqlite3",
        "sre_compile",
        "sre_constants",
        "sre_parse",
        "ssl",
        "stat",
        "statistics",
        "string",
        "stringprep",
        "struct",
        "subprocess",
        "sunau",
        "symtable",
        "sys",
        "sysconfig",
        "syslog",
        "tabnanny",
        "tarfile",
        "telnetlib",
        "tempfile",
        "termios",
        "test",
        "textwrap",
        "threading",
        "time",
        "timeit",
        "tkinter",
        "token",
        "tokenize",
        "tomllib",
        "trace",
        "traceback",
        "tracemalloc",
        "tty",
        "turtle",
        "turtledemo",
        "types",
        "typing",
        "unicodedata",
        "unittest",
        "urllib",
        "uu",
        "uuid",
        "venv",
        "warnings",
        "wave",
        "weakref",
        "webbrowser",
        "winreg",
        "winsound",
        "wsgiref",
        "xdrlib",
        "xml",
        "xmlrpc",
        "zipapp",
        "zipfile",
        "zipimport",
        "zlib",
        "zoneinfo",
    }
)


def _get_python_stdlib_modules() -> frozenset[str]:
    return sys.stdlib_module_names


_PYTHON_STDLIB: frozenset[str] | None = None


def _python_stdlib() -> frozenset[str]:
    global _PYTHON_STDLIB
    if _PYTHON_STDLIB is None:
        _PYTHON_STDLIB = _get_python_stdlib_modules()
    return _PYTHON_STDLIB


# --- JavaScript/TypeScript built-ins ---

JS_BUILTINS = frozenset(
    {
        "console",
        "window",
        "document",
        "global",
        "process",
        "Promise",
        "Array",
        "Object",
        "String",
        "Number",
        "Boolean",
        "Date",
        "Math",
        "JSON",
        "RegExp",
        "Error",
        "Map",
        "Set",
        "setTimeout",
        "setInterval",
        "clearTimeout",
        "clearInterval",
        "fetch",
        "require",
        "module",
        "exports",
        "__dirname",
        "__filename",
    }
)

REACT_HOOKS = frozenset(
    {
        "useState",
        "useEffect",
        "useContext",
        "useReducer",
        "useCallback",
        "useMemo",
        "useRef",
        "useLayoutEffect",
        "useImperativeHandle",
        "useDebugValue",
    }
)

# --- Go built-ins ---

GO_STDLIB_PACKAGES = frozenset(
    {
        "fmt",
        "os",
        "io",
        "net",
        "http",
        "log",
        "math",
        "sort",
        "sync",
        "time",
        "path",
        "bytes",
        "strings",
        "strconv",
        "errors",
        "context",
        "json",
        "xml",
        "csv",
        "html",
        "template",
        "regexp",
        "reflect",
        "runtime",
        "testing",
        "flag",
        "bufio",
        "crypto",
        "encoding",
        "filepath",
        "hash",
        "mime",
        "rand",
        "signal",
        "sql",
        "syscall",
        "unicode",
        "unsafe",
        "atomic",
        "binary",
        "debug",
        "exec",
        "heap",
        "ring",
        "scanner",
        "tar",
        "zip",
        "gzip",
        "zlib",
        "tls",
        "url",
        "user",
        "pprof",
        "trace",
        "ast",
        "build",
        "parser",
        "printer",
        "token",
        "types",
        "cgo",
        "plugin",
        "race",
        "ioutil",
    }
)

GO_BUILTINS = frozenset(
    {
        "make",
        "new",
        "len",
        "cap",
        "append",
        "copy",
        "delete",
        "close",
        "panic",
        "recover",
        "print",
        "println",
        "complex",
        "real",
        "imag",
        "error",
        "nil",
        "true",
        "false",
        "iota",
        "int",
        "int8",
        "int16",
        "int32",
        "int64",
        "uint",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "uintptr",
        "float32",
        "float64",
        "complex64",
        "complex128",
        "string",
        "bool",
        "byte",
        "rune",
        "any",
    }
)


def is_builtin_or_external(
    ref: UnresolvedRef,
    known_names: set[str] | None = None,
) -> bool:
    """Check if a reference is to a built-in or external symbol."""
    name = ref.reference_name
    lang = ref.language

    # --- Python ---
    if lang == "python":
        if name in PYTHON_BUILTINS:
            return True

        stdlib = _python_stdlib()
        top_level = name.split(".")[0]
        if top_level in stdlib:
            return True

        # Dotted calls: list.append, dict.update, etc.
        dot_idx = name.find(".")
        if dot_idx > 0:
            receiver = name[:dot_idx]
            method = name[dot_idx + 1 :]
            if receiver in PYTHON_BUILTIN_TYPES:
                return True
            # Built-in method on a local variable — allow if capitalized
            # receiver matches a known codebase class
            if method in PYTHON_BUILTIN_METHODS:
                capitalized = receiver[0].upper() + receiver[1:]
                if not (known_names and capitalized in known_names):
                    return True

        if name in PYTHON_BUILTIN_METHODS:
            return True

    # --- JavaScript / TypeScript ---
    if lang in ("typescript", "javascript", "tsx", "jsx"):
        if name in JS_BUILTINS:
            return True
        if (
            name.startswith("console.")
            or name.startswith("Math.")
            or name.startswith("JSON.")
        ):
            return True
        if name in REACT_HOOKS:
            return True

    # --- Go ---
    if lang == "go":
        dot_idx = name.find(".")
        if dot_idx > 0:
            pkg = name[:dot_idx]
            if pkg in GO_STDLIB_PACKAGES:
                return True
        if name in GO_BUILTINS:
            return True

    return False
