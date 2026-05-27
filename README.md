# pycodegraph

Python port of [CodeGraph](https://github.com/colbymchenry/codegraph) — a semantic code knowledge graph builder using tree-sitter for AST parsing and SQLite for storage.

Parses source code into nodes (functions, classes, methods, etc.) and edges (calls, imports, extends, etc.), then stores them in a local SQLite database for fast querying.

## Supported Languages

Python, TypeScript, JavaScript, Go, Rust, Java, C, C++

## Quick Start

```bash
# Install
uv add pycodegraph
# or
pip install pycodegraph
```

```python
from pycodegraph import CodeGraph

# Initialize in a project directory
cg = CodeGraph.init("/path/to/project")

# Index all source files
result = cg.index_all()
print(f"Indexed {result.files_indexed} files, {result.nodes_created} nodes")

# Search symbols
nodes = cg.search("MyClass", limit=10)
for node in nodes:
    print(f"  [{node.kind.value}] {node.qualified_name} @ {node.file_path}:{node.start_line}")

# Find callers / callees
callers = cg.get_callers(node.id)
callees = cg.get_callees(node.id)

cg.close()
```

## CLI

```bash
python -m pycodegraph.example /path/to/project
```

## Data Storage

All data is stored in `.codegraph/` within the project directory:

- `codegraph.db` — SQLite database with nodes, edges, and file records
- `config.json` — Project configuration

### InferDB backend

InferDB is supported as a MySQL-compatible relational backend with InferDB FTS.
Install the InferDB extra, then use a MySQL SQLAlchemy URL and mark the logical
backend explicitly:

```bash
uv sync --extra inferdb

python -m pycodegraph.example /path/to/project \
  --db 'mysql+pymysql://user:pass@host:port/db?backend=inferdb'
```

The InferDB backend creates MySQL-compatible tables and uses InferDB's
`PRAGMA create_fts_index` / `match_bm25` support for symbol search through a
DuckDB shadow FTS table.

## Development

```bash
# Install with dev dependencies
uv sync --extra dev

# Run tests
uv run pytest
```

## License

MIT
