# pycodegraph

A semantic code knowledge graph — parse source code, build a graph of symbols and their relationships, persist it to a database, and provide query/search/context-building APIs.

## Language

**Symbol**:
A code entity as it exists in source text — what tree-sitter observes during parsing. A Symbol is the raw, pre-extraction concept; not every Symbol becomes a Node.
_Avoid_: token, AST node, identifier

**Node**:
A code symbol stored in the graph after extraction. A Node is the persisted, queryable representation of a Symbol.
_Avoid_: vertex, entity, record

**Edge**:
A relationship between two Nodes in the graph (e.g., calls, imports, extends, tests). Edges are created during Extraction (structural relationships), Resolution (resolved references), and Test Analysis (test-to-target relationships).
_Avoid_: link, relation, connection

**Reference**:
A mention of one symbol by another, spanning its lifecycle. Starts as an Unresolved Reference (target unknown), becomes an Edge after Resolution.
_Avoid_: cross-reference, dependency (use Edge for the resolved form)

**Kind**:
The classification axis of a Node or Edge. NodeKind (class, function, method…) and EdgeKind (calls, imports, extends, tests…) are the two kind enumerations. Distinct from source-code type hierarchies.
_Avoid_: type, category, label

**Indexing**:
The full pipeline that transforms source files into a queryable graph: scanning → Extraction → Resolution → Test Analysis → persistence.
_Avoid_: parsing, building (too vague)

**Extraction**:
The first stage of Indexing — tree-sitter AST parsing, producing Nodes, Edges, and Unresolved References from source code.
_Avoid_: scanning (that's file discovery), parsing (that's tree-sitter's job)

**Resolution**:
The second stage of Indexing — matching Unresolved References to actual Node definitions, producing additional Edges. A one-way pipeline: extraction feeds resolution, never the reverse.
_Avoid_: linking, binding, resolution pass

**Test Analysis**:
The third stage of Indexing — identifying test Nodes and their tested targets by combining file context, naming conventions, decorator detection, and call graph analysis. Produces `tests` Edges from test Nodes to the Nodes they exercise. Runs after Resolution so that all call and import edges are available.
_Avoid_: test detection, test linking

**Database**:
The persistence layer for the graph — storage and querying. Encompasses Backend implementations, QueryBuilder, and connection management.
_Avoid_: store, repository

**Backend**:
A specific kind of Database storage engine (SQLite, PostgreSQL, InferDB). All Backends share the same QueryBuilder interface.
_Avoid_: driver, adapter, provider

**Context**:
The relevant code neighborhood for a query or symbol. Two shapes: Node Context (a single node's immediate neighbors) and Task Context (a query-oriented subgraph with code blocks and summary).
_Avoid_: snippet, surrounding code (too informal)

**Index** (CodeGraph):
The runtime handle to a code knowledge graph — the entry point for indexing, querying, and searching. CodeGraph is the implementation; Index is the domain concept.
_Avoid_: instance, connection, session
