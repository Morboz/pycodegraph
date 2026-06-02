# `pycodegraph.context` 模块依赖约束

> 最后更新: 2026-06-02

## 1. 模块职责

`pycodegraph.context` 负责构建面向任务的代码上下文：

- 混合搜索（精确符号查找、词干变体前缀匹配、全文搜索）与图遍历（BFS、类型层级扩展）的组合
- 格式化 `TaskContext` 为 markdown、JSON 或 ASCII 树
- 从自然语言查询生成结构化子图，包含入口点、代码块和摘要

**context 不负责**：原始数据存储、搜索策略实现细节、图遍历算法。

## 2. 文件结构与内部依赖

```
context/
├── __init__.py     # Re-export: ContextBuilder, create_context_builder,
│                   #   format_context_as_json, format_context_as_markdown, format_subgraph_tree
├── builder.py      # ContextBuilder 类，混合搜索 + 图遍历 + 评分 + 边恢复
└── formatter.py    # 纯格式化：markdown, JSON, ASCII 树
```

内部依赖方向（必须单向，禁止循环）：

```
builder.py ──→ formatter.py（使用 format_context_as_json, format_context_as_markdown）
__init__.py ──→ builder.py, formatter.py（re-export）
```

formatter.py 无内部依赖，不导入 builder.py。

## 3. 对外依赖（context 导入什么）

| 来源 | 导入符号 | 用途 |
|---|---|---|
| `db.queries` | `QueryBuilder` | 节点查找、边查询、import 解析 |
| `graph.traversal` | `GraphTraverser` | BFS 遍历和类型层级扩展 |
| `search`（公共 API） | `extract_search_terms`, `get_stem_variants`, `is_test_file` | FTS 术语提取、定义前缀匹配、测试文件降权 |
| `search.searcher`（TYPE_CHECKING） | `NodeSearcher` | 精确名称查找、子串匹配、全文搜索（仅类型标注导入） |
| `types` | `BuildContextOptions`, `CodeBlock`, `Edge`, `EdgeKind`, `FindRelevantContextOptions`, `Node`, `NodeKind`, `SearchOptions`, `SearchResult`, `Subgraph`, `TaskContext`, `TraversalOptions` | 核心数据类型 |

注意：`extract_search_terms`、`get_stem_variants`、`is_test_file` 通过 `..search` 公共 API 导入，而非直接从 `..search.query_utils` 导入。`NodeSearcher` 仅在 `TYPE_CHECKING` 守卫下导入，运行时不加载 `search.searcher` 模块。

## 4. 被依赖（谁导入 context）

| 消费者 | 导入的符号 |
|---|---|
| `codegraph.py` | `ContextBuilder`（通过构造器注入，`searcher` 参数由 `_create_components` 创建并传入） |
| `tests/test_inferdb_queries.py` | `ContextBuilder` |

## 5. 约束（Constrains）

### C1: 🔒 context 禁止反向依赖上层业务模块

```
context 不得导入 codegraph, extraction, resolution, integrations
```


🔒 契约：`context-no-business-imports`（配置见 `.importlinter`）


### C2: builder.py → formatter.py 单向，formatter 不导入 builder

formatter.py 是纯格式化模块，无副作用——仅将 `TaskContext`/`Subgraph` 数据结构转换为字符串表示。builder.py 使用 formatter 的函数，但 formatter 永远不导入 builder。

### C3: ContextBuilder 使用构造器注入

`ContextBuilder` 从调用者（`CodeGraph._create_components`）接收 `QueryBuilder`、`GraphTraverser` 和 `NodeSearcher`，全部通过构造器参数注入。`NodeSearcher` 作为共享实例，在 `_create_components` 中创建后同时注入到 `CodeGraph` 和 `ContextBuilder`。这使得模块可测试，但也与 `QueryBuilder`/`GraphTraverser`/`NodeSearcher` 三元组紧密耦合。

### C4: find_relevant_context 实现四步混合搜索管道

精确匹配 → 词干变体前缀 → FTS → 合并，附带多个后处理阶段（co-location 提升、测试降权、import 解析、类型层级扩展、BFS 遍历、单文件多样性上限、非生产节点上限、边恢复）。

## 6. 依赖图（当前状态）

```mermaid
graph TD
    types["types"]
    db["db"]
    graph["graph"]
    search["search"]

    context["context"] --> types
    context --> db
    context --> graph
    context --> search

    codegraph["codegraph"] --> context
```

**关键约束方向**: context → types/db/graph/search（单向），context ✗→ codegraph/extraction/resolution/integrations（禁止反向）。
