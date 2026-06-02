# `pycodegraph.graph` 模块依赖约束

> 最后更新: 2026-06-02

## 1. 模块职责

`pycodegraph.graph` 负责图遍历算法和高级查询组合：

- **低层遍历**（`GraphTraverser`）：BFS、DFS、调用图（get_callers / get_callees / get_call_graph）、类型层级、使用查找（find_usages）、影响半径、最短路径、祖先链、子节点查找
- **高层查询管理**（`GraphQueryManager`）：上下文检索、文件依赖/被依赖分析、导出符号查询（get_exported_symbols）、模块结构（get_module_structure）、死代码检测、循环依赖检测、限定名搜索、子图过滤、节点指标

**graph 不负责**：数据存储（由 `db` 承担）、搜索策略编排、代码解析。

## 2. 文件结构与内部依赖

```
graph/
├── __init__.py     # Re-export: GraphQueryManager, GraphTraverser
├── traversal.py    # 低层遍历算法（GraphTraverser）
└── queries.py      # 高层查询管理（GraphQueryManager），内部创建并持有独立 GraphTraverser
```

内部依赖方向（必须单向，禁止循环）：

```
queries.py ──→ traversal.py（GraphTraverser）
__init__.py ──→ queries.py, traversal.py（re-export）

traversal.py 不导入 queries.py（仅依赖 db.queries.QueryBuilder 和 types）。
```

## 3. 对外依赖（graph 导入什么）

| 来源 | 导入符号 | 用途 |
|---|---|---|
| `db.queries` | `QueryBuilder` | 数据访问层（get_node_by_id, get_incoming_edges, get_outgoing_edges 等） |
| `types` | `Edge`, `EdgeKind`, `Node`, `NodeKind`, `Subgraph`, `TraversalOptions` | 遍历层核心数据类型 |
| `types` | `Context`, `Edge`, `EdgeKind`, `Node`, `NodeKind`, `Subgraph` | 查询层核心数据类型 |

## 4. 被依赖（谁导入 graph）

| 消费者 | 导入的符号 | 说明 |
|---|---|---|
| `codegraph.py` | `GraphQueryManager`, `GraphTraverser` | `codegraph.py` 中 `_create_components` 独立创建 `GraphTraverser(queries)` 实例，与 `GraphQueryManager` 内部创建的 `GraphTraverser` 是不同的对象实例 |
| `context/builder.py` | `GraphTraverser`（从 `..graph.traversal` 导入） | `ContextBuilder` 通过构造器注入接收 `GraphTraverser` 实例，不再自行创建 |

**注意：GraphTraverser 实例重复问题** — 当前架构中存在两处独立的 `GraphTraverser` 创建：
1. `codegraph._create_components()` 中创建 `traverser = GraphTraverser(queries)`，注入到 `CodeGraph._traverser` 和 `ContextBuilder`
2. `GraphQueryManager.__init__` 中创建 `self._traverser = GraphTraverser(queries)`，仅供 `GraphQueryManager` 内部使用

两者共享同一个 `QueryBuilder`，功能等价但不是同一实例。

## 5. 约束（Constrains）

### C1: 🔒 graph 禁止反向依赖上层业务模块

```
graph 不得导入 codegraph, context, extraction, resolution, search, integrations, config
```


🔒 契约：`graph-no-business-imports`（配置见 `.importlinter`）

### C2: 分层架构 — traversal 低层，queries 高层

- `traversal.py` 是低层（基于 `QueryBuilder` 的纯图算法）
- `queries.py` 是高层（组合遍历 + 原始查询为应用级操作）
- 禁止反向依赖：`traversal.py` 不导入 `queries.py`

### C3: 构造器注入 QueryBuilder

`GraphTraverser` 和 `GraphQueryManager` 均在构造时接收 `QueryBuilder` 实例，而非自行创建或访问全局对象，保证可测试性和与数据库层的解耦。

### C4: GraphQueryManager 内部持有 GraphTraverser（非共享实例）

`GraphQueryManager.__init__` 内部自行创建 `self._traverser = GraphTraverser(queries)`，仅供自身使用。**`traverser` 只读属性已被移除**，外部消费者不再通过 `GraphQueryManager` 获取 `GraphTraverser`。

外部消费者（如 `ContextBuilder`）通过构造器注入接收独立的 `GraphTraverser` 实例，该实例由 `codegraph._create_components()` 创建并分发。

### C5: 统一的 Subgraph 输出契约

所有遍历方法返回 `Subgraph` 数据类实例（nodes dict, edges list, roots list），建立跨模块一致输出契约。

例外：`get_callers`、`get_callees`、`find_usages` 返回 `list[tuple[Node, Edge]]`（轻量级调用，无需 Subgraph 封装）。

### C6: 无直接数据库访问

graph 模块从不导入 SQLAlchemy 或直接操作 SQL，所有数据访问通过 `pycodegraph.db.queries.QueryBuilder` 抽象。

## 6. 依赖图（当前状态）

```mermaid
graph TD
    types["types"]
    db["db"]

    graph["graph"] --> types
    graph --> db

    codegraph["codegraph"] --> graph
    context["context"] --> graph
```

**关键约束方向**: graph → types/db（单向），graph ✗→ codegraph/context/extraction/resolution/search/integrations/config（禁止反向）。
