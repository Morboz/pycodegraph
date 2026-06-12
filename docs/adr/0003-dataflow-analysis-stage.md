# Dataflow Analysis as a fourth indexing stage with independent storage

We added a fourth stage to the indexing pipeline — Dataflow Analysis — that runs after Test Analysis and extracts intra-procedural definition-use chains from function bodies. We chose a fully independent storage and traversal layer (separate `dataflow_edges` table, line-number endpoints instead of Node IDs) over embedding dataflow into the existing `edges` table because dataflow edges connect source positions (statements), not symbols (Nodes), and forcing them into the Node-ID-based `edges` table would pollute every existing query path with filtering logic for non-Node endpoints. Python-only for the MVP; other languages to follow.

## Considered Options

- **Statement Nodes in `nodes` table**: Rejected — Statement is not a Symbol (no name, no qualified_name, no visibility), would bloat the nodes table by 5-10x, and most Node fields would be empty/meaningless.
- **Python `ast` module instead of tree-sitter**: Rejected — breaks pycodegraph's tree-sitter-only architecture; the Store/Load distinction can be inferred from tree-sitter position (left-hand side of assignment = Store). Keeping tree-sitter preserves multi-language extension potential.
- **Two directed edges (`DefUse` + `UseDef`)**: Rejected — pycodegraph's edge querying already supports bidirectional traversal (`get_outgoing_edges` / `get_incoming_edges`). One `DATAFLOW` edge (def → use) serves both forward and backward slicing with zero redundancy.
- **Cross-function scope tracking**: Rejected for MVP — inter-procedural dataflow analysis is a research-grade problem. Parameters, `global`, and `nonlocal` serve as explicit boundary markers ("this value came from outside the function"), which is sufficient for LLM consumers.
