# Test Analysis as a separate indexing stage

We added a third stage to the indexing pipeline — Test Analysis — that runs after Resolution and produces `tests` Edges linking test functions/methods to the production code they exercise. We chose a separate stage over embedding this logic in Resolution because test-target identification requires synthesizing information across multiple already-resolved edges (imports, calls), which is fundamentally different from Resolution's job of resolving individual UnresolvedReferences. Python only for the MVP; other languages to follow.
