# `pycodegraph.db` 模块依赖约束

> 最后更新: 2026-06-01

## 1. 模块职责

`pycodegraph.db` 是持久层，负责：

- 数据库连接生命周期管理（`DatabaseConnection`）
- Backend 抽象接口与注册表（`backend.py`）
- SQLAlchemy Core 表定义（`tables.py`）
- 内置后端实现 — SQLite / PostgreSQL / InferDB（`backends/`）
- 通用数据查询与写入（`QueryBuilder`）
- LRU 缓存（`_cache.py`）

**db 不负责**：搜索策略选择、评分算法、查询文本解析。这些由 `pycodegraph.search` 模块承担。

## 2. 文件结构与内部依赖

```
db/
├── __init__.py       # DatabaseConnection, ensure_inferdb_duck_schema, metadata（re-exports）
├── _cache.py         # LRUCache[T]（包内私有）
├── backend.py        # Backend ABC + 注册表 + resolve/prepare
├── tables.py         # 表定义，无内部导入
├── queries.py        # 导入 _cache, backend, tables, ..types
└── backends/
    ├── __init__.py   # 导入并注册三个内置后端
    ├── sqlite.py     # SQLiteBackend（schema + query dialect）
    ├── postgresql.py # PostgreSQLBackend
    └── inferdb.py    # InferDBBackend + ensure_inferdb_duck_schema + 私有工具函数
```

内部依赖方向（必须单向，禁止循环）：

```
backend.py ──→ backends/* ──→ queries.py（通过 get_backend）
    │                              ↑
tables ────────────────────────────┘
_cache ──→ queries
__init__ ──→ backend, backends（触发注册）, tables
```

## 3. 对外依赖（db 导入什么）

| 来源 | 导入符号 | 用途 |
|---|---|---|
| `..types` | `Edge`, `EdgeKind`, `FileRecord`, `Language`, `Node`, `NodeKind`, `UnresolvedReference` | 行↔域对象转换 |

`types` 是项目内最底层的纯数据模块，无外部依赖，db 依赖它是安全的。

## 4. 被依赖（谁导入 db）

| 消费者 | 导入的符号 |
|---|---|
| `codegraph.py` | `DatabaseConnection`（从 `db`）, `QueryBuilder`（从 `db.queries`） |
| `context/builder.py` | `QueryBuilder` |
| `extraction/orchestrator.py` | `QueryBuilder` |
| `graph/queries.py` | `QueryBuilder` |
| `graph/traversal.py` | `QueryBuilder` |
| `resolution/resolver.py` | `QueryBuilder` |
| `search/searcher.py` | `QueryBuilder`（TYPE_CHECKING 延迟导入） |
| `integrations/inferdb.py` | `ensure_inferdb_duck_schema`（从 `db`） |

## 5. 约束（Constrains）

### C1: db 禁止反向依赖上层业务模块 🔒

```
db 不得导入 search, context, extraction, graph, resolution, integrations
```

🔒 **可执行验证**：通过 `import-linter` 的 `forbidden` 契约自动检查。

配置文件：`.importlinter`

```ini
[importlinter:db-no-business-imports]
name = C1: db must not import business modules
type = forbidden
source_modules =
    pycodegraph.db
forbidden_modules =
    pycodegraph.search
    pycodegraph.context
    pycodegraph.extraction
    pycodegraph.graph
    pycodegraph.resolution
    pycodegraph.integrations
```

运行：`lint-imports`

此契约隐含覆盖 C2（db 不得导入 SearchOptions/SearchResult）、C6（db 不得导入 integrations）、C7（db 不得导入 search），因为这些模块整体被禁止。

历史债务：`queries.py` 曾经导入 `..search`，已在 808bb07 中通过引入 `NodeSearcher` 消除。

### C2: db 对 types 的依赖限于数据模型 🔒（被 C1 隐含覆盖）

`..types` 中的纯数据类（Node, Edge, FileRecord 等）是 db 行↔对象转换的必要依赖。但以下类型**不应出现在 db 层**：

- `SearchOptions` — 搜索编排参数，属于 search 层
- `SearchResult` — 搜索评分结果，属于 search 层

当前 `queries.py` 已不导入这两个类型，此约束已满足。

### C3: db.__init__.py 的公开接口由 __all__ 控制

```python
__all__ = [
    "DatabaseConnection",
    "ensure_inferdb_duck_schema",
    "metadata",
    "prepare_engine_url",
    "resolve_backend_name",
]
```

新增公开导出必须同步更新 `__all__`。带 `_` 前缀的函数（如 `backends/inferdb.py` 中的 `_duck_identifier`、`_raw_driver_execute` 等私有工具函数）是内部实现，不属于稳定接口。

### C4: QueryBuilder 只提供数据原语，不承担搜索编排

`QueryBuilder` 的搜索相关方法仅返回原始数据，不做策略选择和评分：

| 方法 | 返回值 | 说明 |
|---|---|---|
| `search_fts()` | `list[tuple]` | FTS 原始行 + 分数 |
| `search_like()` | `list[tuple]` | LIKE 原始行 + 分数 |
| `search_by_filters()` | `list[tuple]` | 按种类/语言过滤的原始行 |
| `find_exact_name_files()` | `set[str]` | 精确名称匹配的文件路径 |
| `find_nodes_by_name_substring()` | `list[Node]` | LIKE 子串匹配的节点列表 |
| `get_all_node_names()` | `list[str]` | 所有去重节点名 |

搜索编排（FTS→LIKE→fuzzy 回退、多信号评分）由 `search.NodeSearcher` 承担，它持有 `QueryBuilder` 引用来调用上述原语。

### C5: _cache.py 是包内私有模块

`_cache.py` 以 `_` 前缀命名，表示包内私有。外部模块不应直接导入 `LRUCache`。如需在 db 包外使用 LRU 缓存，应将其提升为公开模块或独立包。

### C6: InferDB 集成逻辑不属于 db 🔒（被 C1 隐含覆盖）

`InferDBCodeGraphBackend` 位于 `integrations/inferdb.py`，不在 db 包内。它通过 `from ..db import ensure_inferdb_duck_schema` 调用 db 的公开接口。`ensure_inferdb_duck_schema` 是纯 schema 操作，作为 `InferDBBackend` 的 `@staticmethod` 实现，通过 `db.__init__.py` re-export 维持向后兼容。

### C7: Backend 子类统一管理后端特化行为 🔒（被 C1 隐含覆盖）

`InferDBBackend`（在 `backends/inferdb.py` 中）统一封装 InferDB 的 schema 初始化和 SQL 方言生成，这是 db 层的职责。而 `InferDBCodeGraphBackend`（在 `integrations/` 中）负责 InferDB 的生命周期管理（建库、删库），是集成层的职责。两者通过 `?backend=inferdb` URL 参数关联，但不互相导入。

扩展新后端只需：

1. 继承 `Backend`，实现所有 `@abstractmethod`
2. 用 `@register_backend` 注册
3. 不需要修改 `__init__.py`、`queries.py` 中的任何分发逻辑

### C8: backends/inferdb.py 中的私有工具函数

`backends/inferdb.py` 中以 `_` 前缀命名的函数（`_duck_identifier`、`_raw_driver_execute`、`_exec_raw_driver_sql`、`_sql_string_literal`）是 InferDB 后端的内部实现，不属于稳定接口。`integrations/inferdb.py` 因与 InferDB 后端共享相同的底层操作，可通过 `db.backends.inferdb` 导入这些函数。

## 6. 依赖图（当前状态）

```
                    ┌─────────────┐
                    │    types    │  ← 纯数据模型，无外部依赖
                    └──────┬──────┘
                           │
              ┌────────────┼────────────────┐
              │            │                │
              ▼            ▼                ▼
         ┌────────┐  ┌──────────┐    ┌───────────────┐
         │   db   │  │  search  │    │ integrations  │
         │        │  │          │    │               │
         │ 无搜索 │  │ searcher │    │  inferdb.py   │
         │  导入  │  │ ←────── │    │ ←─── db       │
         └───┬────┘  │ ┌────── │    └───────────────┘
             │       │ │       │
             │       │ ▼       │
             └──────→│ db      │
              Query  │ .queries│
              Builder│         │
                     └─────────┘

  消费者（均依赖 db.queries.QueryBuilder）:
    codegraph, context/builder, extraction/orchestrator,
    graph/queries, graph/traversal, resolution/resolver
```

**关键约束方向**: db → types（单向），db ← search/integrations（被依赖），db ✗→ search/context/extraction/graph/resolution（禁止反向）。
