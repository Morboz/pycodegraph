# `pycodegraph.codegraph` 模块依赖约束

> 最后更新: 2026-06-02

## 1. 模块职责

`pycodegraph.codegraph` 是公共 API 表面和主入口，负责：

- `CodeGraph` 类：代码知识图谱的完整生命周期编排（初始化、索引、查询）
- 生命周期工厂方法（`init`, `open`, `open_from_url`）
- 索引操作（`index_all`, `index_file`, `delete_file`, `apply_delta`）
- 查询操作（`search`, `get_node_by_id`, `get_callers`, `get_callees`, `get_callers_deep`, `get_callees_deep`, `get_stats`, `get_all_nodes`, `get_all_edges`, `get_context`, `get_call_graph`, `get_type_hierarchy`, `find_usages`, `get_impact_radius`, `get_file_dependencies`, `get_file_dependents`, `build_context`）
- 生命周期管理（`close`）
- 只读属性（`project_root`, `config`）
- 上下文管理器协议（`__enter__` / `__exit__`）

`__init__.py` 仅重新导出 `CodeGraph` 作为唯一公开符号。`example.py` 是独立的 CLI 演示脚本。

**codegraph 不负责**：具体的数据存储、解析、遍历、搜索实现——全部委托给子模块。

## 2. 文件结构与内部依赖

```
pycodegraph/
├── __init__.py       # 仅 re-export CodeGraph（__all__ = ['CodeGraph']）
├── codegraph.py      # CodeGraph 门面类 + _create_components 辅助函数
└── example.py        # CLI 演示脚本，导入 CodeGraph
```

内部依赖方向：

```
__init__.py ──→ codegraph.CodeGraph
example.py  ──→ pycodegraph.CodeGraph（通过 __init__.py）
```

## 3. 对外依赖（codegraph 导入什么）

| 来源 | 导入符号 | 用途 |
|---|---|---|
| `config` | `CODEGRAPH_DIR`, `CodeGraphConfig`, `create_default_config`, `get_db_url`, `load_config`, `save_config` | 项目配置管理 |
| `context/builder` | `ContextBuilder` | 构建任务上下文 |
| `db` | `DatabaseConnection` | 数据库生命周期管理 |
| `db/queries` | `QueryBuilder` | 图 CRUD 操作 |
| `extraction` | `ExtractionOrchestrator` | 文件扫描与解析（通过 `extraction/__init__.py` re-export） |
| `graph` | `GraphQueryManager`, `GraphTraverser` | 图遍历与查询 |
| `resolution` | `create_resolver` | 引用解析工厂 |
| `search/searcher` | `NodeSearcher` | 多策略搜索（同时通过构造函数注入到 ContextBuilder） |
| `types` | `BuildContextOptions`, `Context`, `Edge`, `IndexResult`, `Node`, `Subgraph` | 核心数据类型 |
| `types`（局部导入） | `SearchOptions` | `search()` 方法内延迟导入 |

## 4. 被依赖（谁导入 codegraph）

| 消费者 | 导入的符号 |
|---|---|
| `pycodegraph.__init__` | `CodeGraph` |
| `pycodegraph.example` | `CodeGraph` |
| `integrations/inferdb.py` | `CodeGraph`（用于 `InferDBCodeGraphBackend.codegraph_factory`，默认为 `CodeGraph` 类型） |
| `tests/*` | `CodeGraph` |

## 5. 约束（Constrains）

### C1: codegraph 是门面模块，禁止被子模块反向依赖

```
codegraph 不得被 db, config, types, context, extraction, graph, resolution, search 导入
```

注意：`integrations/inferdb.py` 是唯一从 codegraph 导入的业务模块，这是允许的，因为 integrations 是叶子模块（见 integrations 约束）。


🔒 契约：`codegraph-no-reverse-deps`（配置见 `.importlinter`）

### C2: 构造函数注入 — 委托优于继承

`CodeGraph` 通过构造函数关键字参数接收所有协作者（`searcher`, `orchestrator`, `traverser`, `graph_manager`, `context_builder`），而非在内部创建它们。构造函数注入确保了依赖关系的显式性和可测试性。`NodeSearcher` 作为单一共享实例创建，同时注入到 `CodeGraph._searcher` 和 `ContextBuilder._searcher`。

注意：`GraphTraverser` 存在重复实例化——`_create_components()` 创建一个实例注入 `CodeGraph._traverser`，而 `GraphQueryManager.__init__` 内部又独立创建了自己的 `GraphQueryManager._traverser`。两个 `GraphTraverser` 实例基于同一个 `QueryBuilder`，功能等价但并非同一对象。

### C3: 工厂方法委托 _create_components 辅助函数

生命周期方法（`init`, `open`, `open_from_url`）是 `@classmethod` 构造器，封装所有设置（配置加载、数据库初始化、查询构建器创建）。三个工厂方法均委托给模块级 `_create_components()` 辅助函数来构建所有协作者对象，再通过构造函数注入传入 `CodeGraph`。`_create_components()` 负责创建 `NodeSearcher`、`ExtractionOrchestrator`、`GraphTraverser`、`GraphQueryManager`、`ContextBuilder` 并确保共享实例的正确传递。

### C4: 单一公开导出

`__init__.py` 通过 `__all__` 仅暴露一个符号（`CodeGraph`），强制执行门面模式。

### C5: 索引后解析模式

`index_all()` 和 `apply_delta()` 均遵循两阶段模式：(1) 抽取节点/边，(2) 通过 `create_resolver()` 解析跨文件引用。解析仅在无致命抽取错误时运行。

## 6. 依赖图（当前状态）

```mermaid
graph TD
    codegraph["codegraph<br/>(门面/根)"]

    codegraph --> config
    codegraph --> types
    codegraph --> db
    codegraph --> extraction
    codegraph --> graph
    codegraph --> search
    codegraph --> context
    codegraph --> resolution

    integrations["integrations"] --> codegraph
```

**关键约束方向**: codegraph → 所有子模块（单向），子模块 ✗→ codegraph。integrations → codegraph 是唯一例外。
