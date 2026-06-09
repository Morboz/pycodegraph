# PyCodeGraph Explore 问题诊断

## 问题 1：只输出 2 个文件切片 vs TS 版 7 个

### 根因：聚类提取完全没有约束单文件大小，一个巨型文件就吃光了全局 budget

诊断脚本 `diagnose_explore2.py` 的输出揭示了致命问题：

```
Selected files (8):
  [1] tests/admin_filters/tests.py       section_size=2,229     ✅
  [2] django/db/models/query.py          section_size=88,650    💀 超出 max_chars_per_file=6,500 十三倍！
  [3] django/contrib/admin/filters.py    section_size=462       ✅
  [4] tests/admin_changelist/admin.py    section_size=444       ✅
  [5] django/db/backends/base/operations.py  section_size=37,489  💀 超出 5.7 倍
  [6] django/db/models/sql/query.py      section_size=123,401   💀 超出 19 倍
  [7] django/core/management/__init__.py section_size=4,263     ✅
  [8] django/db/models/sql/compiler.py   section_size=104,810   💀 超出 16 倍

total_chars 累计：0 → 2,229 → 90,879 → 91,341 → 91,785 → 129,274 → 252,675 → 256,938
```

**关键发现：8 个文件都被选中了（选对了！），但输出只显示了 2 个。**

原因是 `engine.py` 第 307-370 行的 clustered 提取路径：

```python
# engine.py:319-324
ranked = sorted(clusters, key=lambda c: c.importance, reverse=True)
file_budget = min(
    budget.max_chars_per_file,        # 6,500
    max(0, budget.max_output_chars - total_chars - 200),  # 24,000 - total
)
```

`file_budget` 对第一个文件来说是 `min(6,500, 23,800) = 6,500`，看起来合理。
但问题在于 **cluster 选择逻辑完全不执行 file_budget**：

```python
# engine.py:326-333
selected_clusters: list[FileCluster] = []
projected = 0
for cluster in ranked:
    est = (cluster.end_line - cluster.start_line + 1) * 60
    if not selected_clusters or projected + est <= file_budget:
        selected_clusters.append(cluster)
        projected += est
```

Bug 分析：
1. **`not selected_clusters` 条件**：第一个 cluster 永远被选中，不管它有多大。如果 40 个节点的 `gap_threshold=12` 将它们聚成 1 个巨型 cluster，这个 cluster 覆盖 1500+ 行，估算 `(1500)*60 = 90,000` 字符，远超 `file_budget = 6,500`
2. **`cluster_nodes_in_file` 的 gap 合并**：`query.py` 有 40 个节点在 subgraph 中，gap_threshold=12，Django ORM 的方法密度很高（方法间距通常 <12 行），所以几乎所有节点被合并成 1-2 个巨型 cluster，覆盖了文件的绝大部分
3. **之后的所有 cluster 都因为 `projected + est > file_budget` 被跳过**

然后 `extract_source_with_line_numbers` 对这 1 个巨型 cluster 提取了 88,650 字符——是 `max_chars_per_file` 的 13 倍！

接着 `format_source_section` 生成 `section_size = 88,650`，因为 `is_necessary=True`（文件含有 named nodes），90% budget 检查也不起作用：

```python
# engine.py:293-299
is_necessary = any(
    n.id in entry_node_ids or n.id in named_node_ids for n in file_nodes
)
if (
    not is_necessary                                    # ← 永远 False
    and total_chars + len(section) > budget.max_output_chars * 0.9
):
    remaining_files.append(...)
    continue
```

`is_necessary = True` 时 90% 检查被跳过，section 被无条件添加。

### 硬天花板截断

最终 total_chars 达到 256,938，远超 `hard_ceiling = min(24000 * 1.5, 25000) = 25000`。

截断逻辑在 `engine.py:400-417`：
```python
hard_ceiling = min(int(budget.max_output_chars * 1.5), 25_000)
if len(output) > hard_ceiling:
    # Cut at file section boundary
    last_section = cut.rfind("\n#### ")
    boundary = last_section if last_section > ceiling * 0.5 else cut.rfind("\n")
```

它从 256,938 字符中截取到 25,000 字符内，在最后一个 `#### ` 边界处切割。因为 `query.py` 的 section 就占了 ~88K 字符，切割点落在 `query.py` 的 section 内部，所以只保留了：
- tests/admin_filters/tests.py（2,229 字符）
- django/db/models/query.py 的一部分（~22,000 字符）

**结论：compiler.py、operations.py、sql/query.py 等文件虽然被选中，但被 query.py 的巨型 section 吃光了 budget 后被硬天花板截断掉了。**

---

## 问题 2：缺少 Blast Radius、Explore Budget 提示、"Not shown above" 等

### 根因：这些功能根本没有实现

看 `engine.py` 的结构，这些功能虽然有模块文件（`blast_radius.py`、`formatter.py` 中的 `format_remaining_files` 等），但 `ExploreOptions` 的默认值把它们关闭了：

```python
# types.py ExploreOptions
class ExploreOptions:
    include_blast_radius: bool = True    # ✅ 默认开启
    include_flow: bool = True            # ✅ 默认开启
    include_relationships: bool = True   # ✅ 默认开启
```

但实际上 Blast Radius 和 "Not shown above" 仍然没有出现在输出中。原因是：

1. **Blast Radius 被截断了**：blast_radius 的输出在源码 section 之前，但硬天花板截断发生在最终 output 上。如果 blast_radius 内容在 25,000 截断点之前，它就幸存了；如果 query.py 的源码太长把它挤出去了，就没有了。

2. **"Not shown above" 依赖于 `remaining_files` 列表**：在渲染循环中，所有因为 budget 不够而跳过的文件被添加到 `remaining_files`。但由于大多数文件被 `is_necessary=True` 绕过了 90% 检查，`remaining_files` 几乎为空。只有在截断之后那些完全没被渲染的文件才算 remaining——但这些被截断逻辑直接丢掉了，没有收集到 remaining_files 中。

3. **Explore Budget 提示**：TS 版有 `budget.includeBudgetNote` 标志和专门的 `getExploreBudget()` 函数。Py 版的 `ExploreOutputBudget` 没有这个字段，`formatter.py` 也没有对应的格式化函数。**根本没实现。**

---

## 对比 TS CodeGraph 是如何避免这些问题的

TS 版有三层防护，Py 版全部缺失：

### 防护 1：Envelope node 过滤

```typescript
// tools.ts:2230-2247
const ENVELOPE_KINDS = new Set(['file', 'module', 'class', 'struct', 'interface', 'enum', ...]);
// Drop whole-file envelope nodes (containers covering >50% of the file)
.filter(n => !(ENVELOPE_KINDS.has(n.kind) && (n.endLine - n.startLine + 1) > fileLines.length * 0.5))
```

TS 版在聚类前过滤掉覆盖文件 >50% 的容器节点（如 `QuerySet` class 覆盖 query.py 的 90%），避免它们把所有方法合并成 1 个巨型 cluster。Py 版没有这个过滤。

### 防护 2：Cluster 级别的 budget 强制执行

```typescript
// tools.ts:2357-2373
const fileBudget = Math.min(budget.maxCharsPerFile, Math.max(0, budget.maxOutputChars - totalChars - 200));
for (const rc of rankedClusters) {
    const sectionLen = buildSection(rc.c).length + (chosenIndices.size > 0 ? GAP_MARKER.length : 0);
    if (chosenIndices.size === 0) {  // 只保底第1个cluster
        chosenIndices.add(rc.idx);
        projectedChars += sectionLen;
        continue;
    }
    if (projectedChars + sectionLen > fileBudget) continue;  // ← 强制执行
    chosenIndices.add(rc.idx);
    projectedChars += sectionLen;
}
```

TS 版用 `buildSection(rc.c).length`（实际字符数）而非 `*60` 估算来预算，且第一个 cluster 之后的 cluster 严格受 `fileBudget` 限制。即使第一个 cluster 很大，也不会影响后续 cluster 的选择。

### 防护 3：Skeletonization（骨架化）

```typescript
// tools.ts:2083-2159
// On-spine god-file: keep SPINE methods full, collapse off-path to signatures
if (adaptiveExploreEnabled() && flow.pathNodeIds.size > 0
    && (onSpineGodFile || (!hasSpineNode && isPolymorphicSibling(group.nodes) && !spared))) {
    // Per-symbol view: spine methods → full body, everything else → 1-line signature
}
```

对于巨型文件（如 query.py 3041 行），TS 版识别出它是 "on-spine god-file" 后，切换到 per-symbol 渲染模式：flow 路径上的方法（如 `_fetch_all`、`__iter__`）保留完整 body，其余方法只显示签名行。这样 `query.py` 可能只输出 ~3,000 字符而非 88,650。

---

## 修复建议

### 问题 1 修复（按优先级）

1. **🔴 在聚类前过滤 envelope nodes**：覆盖文件 >50% 的 class/module 节点应该被排除出聚类范围，让内部的方法/函数成为独立的聚类单元。这是最关键的修复——没有它，Django 这种大型类会被聚成 1 个 cluster。

2. **🔴 Cluster 选择的 budget 强制执行**：第一个 cluster 之后，严格按 `file_budget` 截断。如果第一个 cluster 就超了 budget，考虑用 skeletonization 而不是整个输出。

3. **🔴 实现 skeletonization / per-symbol 渲染**：对于大型文件，只输出 named seed / entry point 方法的完整 body，其余方法只显示签名行。这是 TS 版控制 query.py 输出大小的核心机制。

4. **🟡 修复 `is_necessary` 的 budget 绕过**：当前 `is_necessary=True` 时 90% 检查被完全跳过，导致必要文件的 section 可以无限大。TS 版的做法是必要文件也受 per-file budget 限制，只是不受 90% 全局 cap 限制。

### 问题 2 修复

1. **🔴 实现 Explore Budget 提示**：在 `ExploreOutputBudget` 中添加 `includeBudgetNote` 标志，在输出末尾添加 budget 说明。

2. **🟡 确保截断后仍保留 Blast Radius 和 Relationships**：这些结构性信息应在源码 section 之前输出，且截断逻辑不应丢弃它们。

3. **🟡 确保截断后仍有 "Not shown above"**：截断时，把被截断掉的文件收集到 remaining_files 中，而不是静默丢弃。
