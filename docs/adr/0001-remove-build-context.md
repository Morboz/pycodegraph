# 1. Remove `build_context` public API

**Date:** 2026-06-10

## Context

`CodeGraph.build_context()` was the original context-building API, returning symbol-level code blocks from a hybrid search + graph traversal pipeline. It was superseded by `CodeGraph.explore()`, which groups source by file with line numbers, traces call chains, and respects adaptive output budgets.

At the time of removal, `build_context` had zero production consumers — it was only called by integration tests in `test_context.py`.

## Decision

Remove `build_context` entirely (public API on `CodeGraph`, implementation on `ContextBuilder`, and related private methods). Also remove `ContextBuilder.get_code`, which was dead code on the same class.

Do not leave a deprecated shim pointing to `explore` — the two APIs have different signatures and return types, so a drop-in replacement is not possible.

## Consequences

- `ContextBuilder` no longer needs `FileProvider` — its only live method (`find_relevant_context`) does pure graph/search operations.
- `ContextBuilder` becomes an internal implementation detail of `ExploreEngine`, not a top-level collaborator of `CodeGraph`.
- Any external code calling `cg.build_context(...)` will break at compile time. The migration path is to use `cg.explore(...)` with `ExploreOptions`.
