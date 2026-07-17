# Research: pycodegraph 作为 `RequirementEvidenceProvider` 的集成可行性

**Map**: [#99 TOCS 语义契约完整化](https://github.com/Morboz/pycodegraph/issues/99)
**Ticket**: [#113 Research: pycodegraph 作为 RequirementEvidenceProvider 的集成可行性](https://github.com/Morboz/pycodegraph/issues/113)
**Anchored at**: `feat/tocs-semantic-contract-types` branch, commit `e0303df` (post XG-004)
**Consumer**: `/Users/xx/software/wanggen/context-compiler/packages/coding/src/formsy/coding/tocs/evidence_providers.py`

## TL;DR

pycodegraph 已经是消费方的依赖（`uv.lock` 里 pin 到 git commit `180895c`，即 Summary Claims slice 2，**早于 TOCS 语义契约工作**）。消费方的 `PyCodeGraphEvidenceProvider` 实现走的是 pycodegraph 的 **legacy edge-based API**（`CodeGraph.open_from_url` + `search` + `get_callers` + `get_callees` + `get_testers` + `search_claims_fts` + `explore`），**不消费** `SemanticGraphQueryHandler` / `semantic_relations` / `RelationKind` 这些 TOCS 语义层。

| 维度 | 状态 | 备注 |
|---|---|---|
| Protocol 形状（3 方法 + 2 属性） | ✓ 可实现 | consumer 已实现 `PyCodeGraphEvidenceProvider` |
| Legacy CodeGraph API 覆盖 | ✓ 全部存在且稳定 | 7 个方法都在 `CodeGraph` 类上 |
| Node/Edge 字段契约 | ○ 大部分一致，2 个字段缺失 | `edge.scenario_id` / `edge.assertion` 不在 `Edge` 上 |
| 依赖 pin 升级路径 | ○ 需要 bump git ref | 当前 pin `180895c`，要吃到 XG-003/004 要 bump |
| 自动吃到 semantic 层能力 | ○ 透明增强 | XG-003 entity + XG-004 alias 走 store 层，consumer 不改代码也能吃到 |
| DocGraph provider 用 pycodegraph | ✗ 当前不消费 | `JsonDocGraphEvidenceProvider` 读原始 graphify-out JSON，不走 pycodegraph |

## 1. 消费方使用面 vs pycodegraph 提供面

### 1.1 `RequirementEvidenceProvider` Protocol

```python
class RequirementEvidenceProvider(Protocol):
    source_instance_id: str
    source_kind: Literal["code_graph", "doc_graph"]
    def capability_manifest(self) -> TOCSGraphCapabilityManifest: ...
    def collect(self, request, item) -> TOCSEvidenceSourceResult: ...
    def collect_many(self, request, items, *, timeout_seconds) -> list[TOCSEvidenceSourceResult]: ...
```

这是消费方侧定义的 Protocol（在 `evidence_providers.py` 顶部）。pycodegraph 这边不需要"实现"它 —— `PyCodeGraphEvidenceProvider` 类**已经在消费方侧实现了**。本 repo 只需保证它调用的 pycodegraph API 都可用。

### 1.2 `PyCodeGraphEvidenceProvider` 调的 pycodegraph API

| consumer 调用 | pycodegraph 位置 | 状态 |
|---|---|---|
| `CodeGraph.open_from_url(db_url)` | `codegraph.py:227` | ✓ 存在，签名兼容 |
| `graph.search(candidate, limit=8)` | `codegraph.py:431` | ✓ 存在，返回 `list[Node]` |
| `graph.get_callers(node_id)` | `codegraph.py:462` | ✓ 存在，返回 `list[Edge]` |
| `graph.get_callees(node_id)` | `codegraph.py:465` | ✓ 存在，返回 `list[Edge]` |
| `graph.get_testers(node_id)` | `codegraph.py:495` | ✓ 存在，返回 `list[tuple[Node, Edge]]` |
| `graph.search_claims_fts(terms)` | `codegraph.py:449` | ✓ 存在，返回 `list[ClaimHit]` |
| `graph.explore(anchor)` | `codegraph.py:531` | ✓ 存在，返回 `str` |
| `graph.close()` | `codegraph.py:282` | ✓ 存在 |

全部命中。consumer 当前的 `PyCodeGraphEvidenceProvider` 在 pycodegraph 当前分支下能正常跑。

### 1.3 Node/Edge 字段契约

**Node 字段**（consumer 通过 `getattr(node, ..., None)` 访问）：

| 字段 | pycodegraph `Node` 上 | 备注 |
|---|---|---|
| `id` | ✓ | `Node.id: str` |
| `qualified_name` | ✓ | `Node.qualified_name: str` |
| `file_path` | ✓ | `Node.file_path: str` |
| `start_line` | ✓ | `Node.start_line: int` |
| `signature` | ✓ | `Node.signature: str \| None` |

**Edge 字段**：

| 字段 | pycodegraph `Edge` 上 | 备注 |
|---|---|---|
| `source` | ✓ | `Edge.source: str` |
| `target` | ✓ | `Edge.target: str` |
| `line` | ✓ | `Edge.line: int \| None` |
| `kind` | ✓ | `Edge.kind: EdgeKind`（StrEnum） |
| `relation_kind` | ✗ | consumer 的 `_edge_relation()` 通过 `getattr(edge, "relation_kind", None)` 取，会落到下一个 fallback |
| `relation` | ✗ | 同上 fallback |
| `type` | ✗ | 同上 fallback |
| `scenario_id` | ✗ | **关键 gap** — 不在 `Edge` 上 |
| `assertion` | ✗ | **关键 gap** — 不在 `Edge` 上 |

**Gap 影响**：
- `scenario_id` / `assertion` 这两个字段 consumer 的 `_tester_observations()` 用 `getattr(edge, "scenario_id", None)` 访问，**会静默返回 `None`**。所以 `validation_coverage` 这种 question_kind 永远拿不到 `direct` 支持级别（看 `evidence_providers.py:716-723` 的 direct 判定逻辑要 `scenario_id` 和 `expected_assertion` 同时命中）。
- 消费方测试 `test_tocs_p1_evidence_grounding.py:1437-1439` 自己 mock 的 `_ScenarioEdge` 带这两个字段，所以测试通过 —— 但这是 mock，不是真实 pycodegraph。
- 这两个字段对应 TOCS 契约里的 `TESTS_SCENARIO` 关系，**该关系的存储位置是 `semantic_relations` 表（不在 `edges` 表）**，且需要 `scenario entity` 建模。这正是 [#99 map 的 fog of war](https://github.com/Morboz/pycodegraph/issues/99) 里的 "TesScenario / scenario entity" 项 —— 还没拆票。

## 2. 依赖 pin 状态

```
# context-compiler/packages/coding/uv.lock
name = "pycodegraph"
version = "0.1.0"
source = { git = "https://github.com/Morboz/pycodegraph.git#180895ccac684e2f41dfefcb0830ba73d4143a8f" }
```

`180895c` 是 "Summary Claims slice 2 — PostgreSQL backend coverage (#95) (#98)"，**早于 #100 DocGraph adapter 及之后所有 TOCS 工作**。

从 `180895c` 到当前 `feat/tocs-semantic-contract-types` HEAD (`e0303df`)，pycodegraph 新增的 consumer-relevant 能力：
- XG-001~008 跨图 composition（#108）
- XG-003 `semantic_entities` 表（#109）—— consumer 自动吃到（subject resolution 走 canonical_name）
- XG-004 `CROSS_GRAPH_ALIAS`（#110）—— consumer 自动吃到（subject 扩展透明）
- DocGraph adapter 的 6 个 `.rst` 细提取（#102 / #106）

要让消费方吃到这些，需要：
1. **合并 `feat/tocs-semantic-contract-types` 到 main**，或打新 tag
2. **更新 `context-compiler/packages/coding/pyproject.toml`** 的 `pycodegraph` git ref（消费方 repo 操作，不在本 repo scope）

## 3. pycodegraph 语义层是否被消费

**目前完全没被消费**。`PyCodeGraphEvidenceProvider` 走的是 `nodes` / `edges` / `summary_claims` 三张表，不读 `semantic_relations` / `semantic_entities` / `semantic_dataset_manifests`。

**这不一定是个 gap**。语义层是**透明增强**：
- XG-003 把 CodeGraph + DocGraph 实体都落到 `semantic_entities` 表，subject resolution 走 `canonical_name`。consumer 调 `graph.search()` 仍然命中 `nodes` 表 —— 但如果消费方未来想换成 `SemanticGraphQueryHandler.query()`，路径已铺好。
- XG-004 的 alias 走 `semantic_relations` 表 + `read_cross_graph_aliases`，consumer 当前不读这张表，所以 alias 不起作用。要让 consumer 吃到 alias，需要消费方那边把 `_exact_symbol_nodes()` 升级为也查 `semantic_entities` 表 + alias 扩展。

**所以**：当前状态下，消费方升级 pycodegraph 版本后，**几乎吃不到 XG-003/004 的好处**，除非也升级 `PyCodeGraphEvidenceProvider` 的实现。这是消费方 repo 的迁移工作，不在本 repo scope。

## 4. DocGraph 端：`JsonDocGraphEvidenceProvider`

消费方有第二个 provider `JsonDocGraphEvidenceProvider`，**完全不依赖 pycodegraph**：直接 `json.loads(self.graph_path.read_text())` 读 graphify-out 的 `.json` 文件，按 `label`/`norm_label` 字符串匹配节点，按 `link.relation` 字符串提取关系。

这个 provider 是"JSON 直读"路径，与本 repo 的 `GraphifyAdapter` 没有交集。如果要把它迁到 pycodegraph 的 DocGraph adapter 输出，需要：
- 消费方侧把 provider 改成走 `SemanticGraphQueryHandler.query()` with `expected_relation=DOCUMENTS_*`
- pycodegraph 侧需要 #111 (XG-005 revision 对齐) + #112 (capability 聚合) 才能给 DocGraph 一个完整可用的 capability manifest

这同样是消费方 repo 的迁移工作。

## 5. pycodegraph 这边需要做什么

**短期（本 repo 内）**：什么都不需要做。consumer 用的 legacy API 已经稳定，pycodegraph 不破坏它即可。

**中期（map 内已有 ticket）**：
- [#111 XG-005: DocGraph revision 对齐](https://github.com/Morboz/pycodegraph/issues/111) — 让 DocGraph 的 `source_revision` 字段有意义，consumer 的 `revision_alignment` 检查才有真实值
- [#112 manifest 组合与跨图 capability 聚合](https://github.com/Morboz/pycodegraph/issues/112) — 让消费方能从一个 capability manifest 看到两图合并后的能力集

**长期（fog of war，未拆票）**：
- `scenario entity` 建模 + `TESTS_SCENARIO` 关系（解决 `edge.scenario_id` / `edge.assertion` gap 的根本路径）
- 消费方侧迁移：`PyCodeGraphEvidenceProvider` 从 legacy API 切到 `SemanticGraphQueryHandler`（消费方 repo 的事）

## 6. 结论

| Question 项 | 答案 |
|---|---|
| 依赖 pin | 当前 `180895c`（pre-TOCS）；要吃到新能力需 bump |
| API 覆盖 | ✓ 7 个 legacy 方法都存在且契约稳定 |
| 数据契约 | ✓ Node 字段全中；Edge 缺 `scenario_id` / `assertion`（map 已记为 fog） |
| 可选增强（semantic 层） | 当前不消费；升级路径已铺，需消费方侧迁移 |
| DocGraph 端 | consumer 走原始 JSON，不消费 pycodegraph；可保持现状 |

**Recommendation**：本 repo 不需要为此开新 ticket。继续推 #111 / #112 即可。`scenario_id` / `assertion` 的根本解决路径是 `TESTS_SCENARIO` fog，等它拆票后自然落地。**消费方侧的 provider 迁移是另一个 repo 的事，不属于本 map 范围**。
