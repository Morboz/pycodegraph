# pycodegraph

Python port of [CodeGraph](https://github.com/colbymchenry/codegraph) — a semantic code knowledge graph builder using tree-sitter for AST parsing and local relational storage.

Parses source code into nodes (functions, classes, methods, etc.) and edges (calls, imports, extends, etc.), then stores them in a queryable database for symbol search, graph traversal, and natural-language context building.

## Supported Languages

Python, TypeScript, JavaScript, Go, Rust, Java, C, C++

## Quick Start

```bash
# Install
pip install pycodegraph
# or
uv add pycodegraph
```

```python
from pycodegraph import CodeGraph

cg = CodeGraph.init("/path/to/project")
result = cg.index_all()
print(f"Indexed {result.files_indexed} files, {result.nodes_created} nodes")

nodes = cg.search("MyClass", limit=10)
for node in nodes:
    print(f"[{node.kind.value}] {node.qualified_name} @ {node.file_path}:{node.start_line}")

if nodes:
    callers = cg.get_callers(nodes[0].id)
    callees = cg.get_callees(nodes[0].id)
    print(f"callers={len(callers)} callees={len(callees)}")

cg.close()
```

## CLI

```bash
python -m pycodegraph.example /path/to/project
```

## Database Setup and Storage Layout

### Default SQLite project layout

`CodeGraph.init("/path/to/project")` creates a `.codegraph/` directory inside the indexed project:

- `.codegraph/codegraph.db` — main SQLite database
- `.codegraph/config.json` — persisted `CodeGraphConfig`
- `.codegraph/.gitignore` — ignores `*.db`, WAL/SHM files, logs, and cache artifacts

During active writes, SQLite may also create `codegraph.db-wal` and `codegraph.db-shm` next to `codegraph.db`.

### SQLite initialization flow

```python
from pycodegraph import CodeGraph

cg = CodeGraph.init("/repo")      # create .codegraph/, config.json, codegraph.db
result = cg.index_all()            # populate nodes / edges / files tables
same = CodeGraph.open("/repo")    # reopen later using saved config
same.close()
cg.close()
```

SQLite is the default backend when `config.db_url` is unset. On initialization/open, pycodegraph also enables:

- foreign keys
- WAL mode
- FTS5-backed symbol search (`nodes_fts`)
- `lower(name)` index for fast exact-name lookup

### InferDB / MySQL-compatible backend

InferDB is the supported MySQL-compatible external backend. Install the optional dependency first:

```bash
pip install 'pycodegraph[inferdb]'
# or
uv sync --extra inferdb
```

Then pass a MySQL SQLAlchemy URL with the logical backend marker:

```python
from pycodegraph import CodeGraph

cg = CodeGraph.init(
    "/repo",
    {
        "db_url": "mysql+pymysql://<user>:<password>@<host>:3307/codegraph_demo?backend=inferdb",
    },
)
```

Notes:

- `?backend=inferdb` is required so pycodegraph selects the InferDB dialect.
- Plain MySQL without the InferDB backend marker is not a supported write backend.
- InferDB stores the main tables in MySQL-compatible storage and maintains a DuckDB shadow FTS index for search.
- In real integrations, prefer environment variables or a secrets manager over hardcoding credentials in code.

If your integration owns database provisioning, use `InferDBCodeGraphBackend`:

```python
from pycodegraph import CodeGraph, InferDBCodeGraphBackend

backend = InferDBCodeGraphBackend(
    host="127.0.0.1",
    port=3307,
    user="test",
)

db_url = backend.ensure_database("cg_1234abcd")
cg = CodeGraph.init("/repo", {"db_url": db_url})
```

Useful helper methods:

- `ensure_database(name)` — create DB + DuckDB schema and return a ready-to-use `db_url`
- `existing_database_url(name)` — return `None` instead of creating a missing DB
- `open_codegraph(name)` — open a `CodeGraph` directly for read/query paths
- `drop_database(name)` — remove an InferDB database and its shadow schema

## Database Schema Overview

Core tables are defined in `src/pycodegraph/db/tables.py`.

### `nodes`

One row per extracted symbol.

| Column | Meaning |
| --- | --- |
| `id` | Stable symbol id (primary key) |
| `kind` | Symbol kind, such as `function`, `class`, `method` |
| `name` / `qualified_name` | Short name and fully qualified name |
| `file_path` / `language` | Source file and detected language |
| `start_line` / `end_line` / `start_column` / `end_column` | Source span |
| `docstring` / `signature` / `visibility` | Optional semantic metadata |
| `is_exported` / `is_async` / `is_static` / `is_abstract` | Flags persisted as ints |
| `decorators` / `type_parameters` | JSON-like text payloads when available |
| `updated_at` | Last write timestamp in ms |

### `edges`

Relationships between nodes.

| Column | Meaning |
| --- | --- |
| `source` / `target` | Source and target node ids |
| `kind` | Relationship kind: `calls`, `imports`, `extends`, `implements`, `references`, ... |
| `metadata` | Optional JSON/text metadata |
| `line` / `col` | Source position for the relationship |
| `provenance` | Optional resolver provenance |

### `files`

Per-file indexing metadata used for incremental indexing.

| Column | Meaning |
| --- | --- |
| `path` | Relative project path (primary key) |
| `content_hash` | SHA-256 of indexed content |
| `language` | Detected file language |
| `size` / `modified_at` | File size and filesystem mtime |
| `indexed_at` | Last successful index timestamp in ms |
| `node_count` | Number of extracted nodes for that file |
| `errors` | Optional serialized extraction errors |

Related internal tables:

- `unresolved_refs` — unresolved references collected during indexing
- `project_metadata` — reserved project metadata
- `schema_versions` — schema migration marker

## Indexing Strategy and Re-indexing

### How incremental indexing works

`index_all()` scans files, parses supported sources, and compares each file's current SHA-256 hash with the stored `files.content_hash`.

- unchanged file → skipped
- changed file → old rows for that path are deleted, then nodes/edges/file metadata are reinserted
- new file → inserted normally

This makes repeated `index_all()` runs incremental by default.

### When to do a clean rebuild

Use a fresh database instead of incremental indexing when:

- you changed include/exclude patterns in `config.json`
- you switched backends (for example SQLite → InferDB)
- you renamed or deleted many files and want to remove stale paths from the graph
- you want to rebuild after parser/schema changes

SQLite clean rebuild:

```bash
rm -f /path/to/project/.codegraph/codegraph.db \
      /path/to/project/.codegraph/codegraph.db-wal \
      /path/to/project/.codegraph/codegraph.db-shm
```

Then call `CodeGraph.init("/path/to/project")` again and re-run `index_all()`.

### Search index maintenance

- SQLite: FTS5 triggers keep `nodes_fts` in sync after inserts/updates/deletes.
- PostgreSQL: generated `tsvector` + GIN/trigram indexes are initialized automatically.
- InferDB: after node writes, pycodegraph rebuilds the DuckDB shadow FTS table and runs `PRAGMA create_fts_index(...)`.

## Query API Reference

All query methods below are exposed on `CodeGraph` in `src/pycodegraph/codegraph.py`.

### Symbol lookup

| Method | Parameters | Returns | Example |
| --- | --- | --- | --- |
| `search(query, limit=20)` | `query: str`, `limit: int` | `list[Node]` | `cg.search("QueryBuilder", limit=5)` |
| `get_node_by_id(node_id)` | `node_id: str` | `Node | None` | `cg.get_node_by_id(node.id)` |

```python
hits = cg.search("build_context", limit=5)
node = cg.get_node_by_id(hits[0].id) if hits else None
```

### Call graph traversal

| Method | Parameters | Returns | Example |
| --- | --- | --- | --- |
| `get_callers(node_id)` | `node_id: str` | `list[Edge]` | `cg.get_callers(node.id)` |
| `get_callees(node_id)` | `node_id: str` | `list[Edge]` | `cg.get_callees(node.id)` |
| `get_callers_deep(node_id, max_depth=1)` | `node_id: str`, `max_depth: int` | `list[tuple[Node, Edge]]` | `cg.get_callers_deep(node.id, max_depth=2)` |
| `get_callees_deep(node_id, max_depth=1)` | `node_id: str`, `max_depth: int` | `list[tuple[Node, Edge]]` | `cg.get_callees_deep(node.id, max_depth=2)` |
| `get_call_graph(node_id, depth=2)` | `node_id: str`, `depth: int` | `Subgraph` | `cg.get_call_graph(node.id, depth=2)` |

```python
call_edges = cg.get_callees(node.id)
for edge in call_edges:
    callee = cg.get_node_by_id(edge.target)
    if callee:
        print("calls", callee.qualified_name)

deep_callers = cg.get_callers_deep(node.id, max_depth=2)
for caller, via in deep_callers:
    print(caller.name, via.kind.value)
```

### Type and usage analysis

| Method | Parameters | Returns | Example |
| --- | --- | --- | --- |
| `get_type_hierarchy(node_id)` | `node_id: str` | `Subgraph` | `cg.get_type_hierarchy(class_node.id)` |
| `find_usages(node_id)` | `node_id: str` | `list[tuple[Node, Edge]]` | `cg.find_usages(node.id)` |
| `get_impact_radius(node_id, max_depth=3)` | `node_id: str`, `max_depth: int` | `Subgraph` | `cg.get_impact_radius(node.id, max_depth=3)` |

```python
usages = cg.find_usages(node.id)
for usage_node, edge in usages:
    print(usage_node.file_path, edge.kind.value)

impact = cg.get_impact_radius(node.id, max_depth=3)
print(len(impact.nodes), len(impact.edges))
```

### File- and node-context helpers

| Method | Parameters | Returns | Example |
| --- | --- | --- | --- |
| `get_file_dependencies(file_path)` | `file_path: str` (relative project path) | `list[str]` | `cg.get_file_dependencies("src/app.py")` |
| `get_file_dependents(file_path)` | `file_path: str` (relative project path) | `list[str]` | `cg.get_file_dependents("src/app.py")` |
| `get_context(node_id)` | `node_id: str` | `Context` | `cg.get_context(node.id)` |
| `build_context(task_input, options=None)` | `task_input: str | dict`, `options: BuildContextOptions | dict | None` | `str | TaskContext` | `cg.build_context("How does indexing work?")` |

```python
ctx = cg.get_context(node.id)
print(ctx.focal.name)
print([n.name for n in ctx.ancestors])

print(cg.get_file_dependencies("src/pycodegraph/codegraph.py"))
print(cg.get_file_dependents("src/pycodegraph/context/builder.py"))
```

## `build_context()` Detailed Usage

### What the hybrid search pipeline does

`build_context()` turns a natural-language task into a `TaskContext` by combining search and graph traversal:

1. extract likely identifiers from the prompt (`CamelCase`, `snake_case`, acronyms, dotted names)
2. exact symbol-name lookup
3. definition prefix matching with stem/title-case variants
4. full-text search through the backend FTS index
5. type-hierarchy expansion for class-like symbols
6. BFS traversal around the best entry points
7. edge recovery between selected nodes

This is why prompts like `"How does BuildContextOptions affect build_context?"` usually work better than a single exact symbol name.

### `BuildContextOptions`

`BuildContextOptions` is defined in `src/pycodegraph/types.py`.

| Option | Default | When to increase | When to decrease |
| --- | --- | --- | --- |
| `max_nodes` | `20` | broader architectural questions | token budget is tight |
| `max_code_blocks` | `5` | code review / debugging prompts | summary-only assistants |
| `max_code_block_size` | `1500` | large functions/classes matter | clients have strict context limits |
| `include_code` | `True` | you need snippets in the result | graph-only analysis is enough |
| `format` | `"markdown"` | use `"json"` for machine consumers | use a non-markdown/json value to get raw `TaskContext` |
| `search_limit` | `3` | ambiguous prompts with multiple candidate symbols | queries already mention precise identifiers |
| `traversal_depth` | `1` | dependency tracing and impact analysis | direct neighbors are enough |
| `min_score` | `0.3` | recall matters more than precision | noisy repos/prompts |

Example:

```python
from pycodegraph.types import BuildContextOptions

options = BuildContextOptions(
    max_nodes=30,
    max_code_blocks=8,
    traversal_depth=2,
    format="json",
)

payload = cg.build_context(
    {
        "title": "Trace indexing flow",
        "description": "Show entry points for incremental indexing and file hashing",
    },
    options,
)
```

### Output modes

- `format="markdown"` — best for LLM prompts, chat replies, and developer-facing summaries
- `format="json"` — best for IDE extensions, automation, and transport over APIs
- any other `format` value — returns the raw `TaskContext` dataclass for in-process Python integrations

Raw object example:

```python
from pycodegraph.types import BuildContextOptions

obj = cg.build_context(
    "Where is QueryBuilder.search_nodes implemented?",
    BuildContextOptions(format="object", include_code=False),
)
print(type(obj).__name__)   # TaskContext
print(obj.stats)
```

### MCP server integration pattern

pycodegraph does not ship an MCP server, but `build_context()` is designed to sit behind one.

```python
from pycodegraph import CodeGraph
from pycodegraph.types import BuildContextOptions

cg = CodeGraph.open("/repo")

def code_context_tool(task: str) -> str:
    return cg.build_context(
        task,
        BuildContextOptions(format="markdown", traversal_depth=2),
    )
```

Recommended pattern:

- keep one `CodeGraph` instance open per indexed project
- return markdown to chat-oriented MCP clients
- return JSON when the MCP client wants structured nodes/edges/files
- reuse `TaskContext.stats` to enforce token and latency budgets

## Integration Guide

### Minimal Python library integration

```python
from pycodegraph import CodeGraph

with CodeGraph.init("/repo") as cg:
    cg.index_all()
    hits = cg.search("AuthService")
    if hits:
        context = cg.build_context(f"How is {hits[0].name} used?")
        print(context)
```

### Lifecycle management

Typical lifecycle is:

1. `CodeGraph.init(project_root, config_overrides=None)`
2. `index_all()` for initial import
3. `search(...)` / graph queries / `build_context(...)`
4. repeat `index_all()` to refresh incrementally
5. `close()` when the process exits

If the project was already initialized, reopen it with `CodeGraph.open(project_root)`.

### Multi-project management

Two common patterns:

- SQLite per repo: each project owns its own `.codegraph/codegraph.db`
- InferDB shared service: create one database per project, branch, or tenant using `InferDBCodeGraphBackend.ensure_database(...)`

Example naming scheme for external DBs:

```python
backend = InferDBCodeGraphBackend.from_env()
project_db = backend.ensure_database("cg_myrepo_main")
other_db = backend.ensure_database("cg_myrepo_feature_x")
```

### Performance reference: what to measure

This repository does not ship fixed benchmark numbers, but pycodegraph exposes the fields you need to publish your own reference data.

```python
result = cg.index_all()
print({
    "files_indexed": result.files_indexed,
    "files_skipped": result.files_skipped,
    "nodes_created": result.nodes_created,
    "edges_created": result.edges_created,
    "duration_ms": result.duration_ms,
})
```

For query latency, measure your own workload around calls such as `search()` and `build_context()`. `TaskContext.stats` also reports node, edge, file, and code-block counts, which is useful when tuning latency versus context size.

## Development

```bash
# Install with dev dependencies
pip install -e '.[dev]'
# or
uv sync --extra dev

# Run tests
pytest
# or
uv run pytest
```

## License

MIT
