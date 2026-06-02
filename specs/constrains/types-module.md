# `pycodegraph.types` 模块依赖约束

> 最后更新: 2026-06-02

## 1. 模块职责

`pycodegraph.types` 是纯数据定义层，负责：

- 图原语数据类（`Node`, `Edge`）
- 节点/边/语言分类枚举（`NodeKind`, `EdgeKind`, `Language`，均使用 `StrEnum`）
- 抽取结果类型（`ExtractionResult`, `ExtractionError`, `UnresolvedReference`, `FileRecord`, `IndexResult`）
- 查询/遍历/搜索/上下文参数与返回类型（`Subgraph`, `TraversalOptions`, `SearchOptions`, `SearchResult`, `Context`, `CodeBlock`, `BuildContextOptions`, `FindRelevantContextOptions`, `TaskContext`）

**types 不负责**：任何业务逻辑、I/O 操作、副作用代码。

## 2. 文件结构与内部依赖

```
types.py    # 单文件模块，包含全部 16 个 dataclass 和 3 个 StrEnum
```

单文件模块，无内部文件间依赖。

## 3. 对外依赖（types 导入什么）

| 来源 | 导入符号 | 用途 |
|---|---|---|
| `dataclasses` | `dataclass`, `field` | 声明所有数据类和默认字段 |
| `enum` | `StrEnum` | 声明 `NodeKind`, `EdgeKind`, `Language` |
| `__future__` | `annotations` | 启用 PEP 604 联合类型语法 |

types 仅依赖 Python 标准库，无任何第三方或项目内依赖。

## 4. 被依赖（谁导入 types）

| 消费者 | 导入的符号 |
|---|---|
| `codegraph.py` | `BuildContextOptions`, `Context`, `Edge`, `IndexResult`, `Node`, `Subgraph`（顶层导入）；`SearchOptions`（延迟导入） |
| `context/builder.py` | `BuildContextOptions`, `CodeBlock`, `Edge`, `EdgeKind`, `FindRelevantContextOptions`, `Node`, `NodeKind`, `SearchOptions`, `SearchResult`, `Subgraph`, `TaskContext`, `TraversalOptions` |
| `context/formatter.py` | `Edge`, `Node`, `Subgraph`, `TaskContext` |
| `db/queries.py` | `Edge`, `EdgeKind`, `FileRecord`, `Language`, `Node`, `NodeKind`, `UnresolvedReference` |
| `extraction/extractor.py` | `Edge`, `EdgeKind`, `ExtractionError`, `ExtractionResult`, `Language`, `Node`, `NodeKind`, `UnresolvedReference` |
| `extraction/grammars.py` | `Language` |
| `extraction/helpers.py` | `NodeKind` |
| `extraction/languages/__init__.py` | `Language` |
| `extraction/languages/go.py` | `NodeKind` |
| `extraction/orchestrator.py` | `Edge`, `ExtractionError`, `ExtractionResult`, `FileRecord`, `IndexResult`, `Language`, `Node`, `UnresolvedReference` |
| `graph/queries.py` | `Context`, `Edge`, `EdgeKind`, `Node`, `NodeKind`, `Subgraph` |
| `graph/traversal.py` | `Edge`, `EdgeKind`, `Node`, `NodeKind`, `Subgraph`, `TraversalOptions` |
| `resolution/_types.py` | `EdgeKind` |
| `resolution/resolver.py` | `Edge`, `EdgeKind`, `Node`, `NodeKind`, `UnresolvedReference` |
| `resolution/import_resolver.py` | `Node`（顶层导入）；`NodeKind`（延迟导入） |
| `resolution/name_matcher.py` | `Node`, `NodeKind` |
| `search/query_parser.py` | `Language`, `NodeKind` |
| `search/searcher.py` | `Language`, `NodeKind`, `SearchOptions`, `SearchResult` |

## 5. 约束（Constrains）

### C1: 🔒 types 是叶子模块，禁止导入项目内任何其他模块

```
types 不得导入 db, config, codegraph, context, extraction, graph, resolution, search, integrations
```


🔒 契约：`types-no-internal-imports`（配置见 `.importlinter`）

### C2: types 仅包含数据定义，不含业务逻辑

文件中只有 `@dataclass` 定义和 `StrEnum` 定义，没有函数、副作用或行为代码。这保证了模块稳定性，使其可被广泛安全导入。

### C3: 单文件模块

所有类型定义位于一个文件中，不做子模块拆分（如 `types/nodes.py`, `types/edges.py`）。这使得导入简洁、类型词汇易于一览。

### C4: 仅依赖标准库

模块仅依赖 `dataclasses`, `enum`, `__future__`，无第三方包依赖，保持类型层轻量和可移植。

### C5: StrEnum 用于序列化

`NodeKind`, `EdgeKind`, `Language` 使用 `StrEnum`（Python 3.11+），其值直接序列化为字符串——对数据库存储和 API 序列化至关重要。

### C6: types 是跨切面类型的唯一真实来源

其他模块定义的领域特定中间类型（如 `resolution._types` 定义 `UnresolvedRef`, `ResolvedRef`, `ResolutionResult`, `ImportMapping`）从 types 导入 `EdgeKind`，但不重新导出 types 的符号。`types.py` 保持跨切面类型的唯一真实来源。

## 6. 依赖图（当前状态）

```mermaid
graph TD
    types["types<br/>(叶子模块)"]

    codegraph["codegraph"] --> types
    context["context"] --> types
    db["db"] --> types
    extraction["extraction"] --> types
    graph["graph"] --> types
    resolution["resolution"] --> types
    search["search"] --> types
```

**关键约束方向**: 所有箭头指向 types（上游 → types），types ✗→ 任何项目内模块。
