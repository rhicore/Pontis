# Financial 数据集工具调用分析

基于 `example_data/bird/dev_databases/financial` 提取日志的深度分析。
数据集：8 张表、57 列、29 个 overlap、105 万行 trans 表。AI 阶段耗时 ~34 分钟。

---

## 一、核心问题总览

| # | 问题 | 影响轮次 | 严重度 | 根因 |
|---|------|---------|--------|------|
| 1 | `::` 遍历找不到表下的列 | ~16 轮（8 子智能体 × 2 轮） | **P0** | 边结构或 glob 匹配问题 |
| 2 | 子智能体 max_rounds 不够 | 50+ 轮（主智能体重做） | **P0** | 宽表列数 > 子智能体可处理量 |
| 3 | 写后仍验证（update_meta 后调 meta） | ~20 轮 | **P1** | 子智能体未遵守效率守则 |
| 4 | `meta(all=true)` 冗余读取 | ~30 轮 | **P1** | 缺少精准读取意识 |
| 5 | 子智能体失败后主智能体无差异重做 | ~40 轮 | **P1** | 无增量检测机制 |
| 6 | 无批量 update_meta | ~50 轮 | **P2** | 每列单独调用 |

---

## 二、逐问题详细分析

### 问题 1：`::` 边遍历找不到表下的列 [P0]

**现象**：每个子智能体都先尝试 `glob "db::table::*.col"`，返回 `No objects found`，再改用 `glob "db::table.*.*.col"` 才成功。

```
# 每个子智能体都重复这个模式（浪费 2 轮）：
L137  glob "financial.sqlite::account.table::*.col"  → No objects found
L139  glob "financial.sqlite::account.*.col"          → 成功
```

甚至在主智能体阶段也出现（L265-266）：
```
glob "financial.sqlite::account.table::*"  → 只返回 financial.sqlite（文件节点）
```

**根因**：`table → col` 的边可能不存在，或 glob 的 `::` 遍历在遇到 `*` 通配符时匹配逻辑有 bug。列实体通过文件系统路径命名（如 `account.account_id.INT.col`），但边遍历依赖于图索引中的边记录。

**影响**：
- 每个子智能体浪费 2 轮（尝试 + 回退），8 个子智能体共浪费 ~16 轮
- 更严重的是，agent 每次都需要"发现"这个模式，增加了认知负担

**建议修复**：

**(a) 修复边遍历**：确保 `table → col` 的双向边存在。如果静态提取阶段没有建这些边，应在 `db_basic` 中补建。这样 `glob "db::table::*.col"` 才能正常工作。

**(b) 在静态提示词中明确推荐 glob 路径模式**：在 `_base.py` 的工具使用策略中加入：

```
### 查找表的列
推荐: glob "db::table.*.*.col"
不推荐: glob "db::table::*.col"   ← 边遍历可能不覆盖列
```

### 问题 2：子智能体 max_rounds 不足 [P0]

**现象**：district 表（16 列）和 trans 表（10 列）的子智能体都撞到 25 轮上限。

```
# district 子智能体：25 轮用完，只写了 ~12 列
L595-605  status: "max_rounds_reached", rounds_used: 25

# trans 子智能体：25 轮用完，只写了 table + 3 列
L846-856  status: "max_rounds_reached", rounds_used: 25
```

**影响**：
- 主智能体被迫手动重做未完成的工作
- district 表：主智能体额外用了 ~35 轮（L606-636）重写已完成的 + 补充未完成的
- trans 表：主智能体额外用了 ~20 轮（L857-908）
- loan 表：也撞了 25 轮（L1037-1047），主智能体又用了 ~20 轮重做

**为什么 25 轮不够**：
一个 16 列表的子智能体流程：
1. meta(table) → 1 轮
2. meta(table, all) → 1 轮（冗余）
3. glob("table::*.col") → 1 轮（失败）
4. glob("table.*.*.col") → 1 轮（成功）
5. meta(col, all=true) × 16 → 16 轮
6. meta(table, ["brief","detail"]) → 1 轮
7. update_meta(table) → 1 轮
8. update_meta(col) × 16 → 16 轮
9. meta(table, ["brief","detail"]) 验证 → 1 轮

总计：**~39 轮**，远超 25 轮限制。

**建议修复**：

**(a) 子智能体按需分配 max_rounds**：主智能体根据列数动态计算：
```python
# 在主智能体 prompt 中添加
max_rounds = max(25, column_count * 3 + 5)
```

**(b) 减少子智能体的冗余调用**（结合问题 3、4 的修复）：
如果去掉验证调用和 `meta(all=true)`，一个 16 列表的子智能体只需：
1. glob → 1 轮（找到列）
2. update_meta(table) → 1 轮
3. update_meta(col) × 16 → 16 轮（主智能体已传入列信息，不需要再读）

总计：~18 轮，25 轮足够。

**(c) 或者按批分配**：子智能体分批处理列（每批 ≤ 8 列），任务描述中明确告知"只处理这 N 列"。

### 问题 3：update_meta 后仍验证 [P1]

**现象**：子智能体在 `update_meta` 返回写入值后，仍然调用 `meta` 验证。

```
# account 子智能体（L326-351）：
L326  update_meta(account.table, {brief, detail})   → 返回写入值 ✓
L330  update_meta(account_id, {brief, detail})       → 返回写入值 ✓
...
L346  meta(account.table, ["brief", "detail"])       → 验证（冗余！）
L349  meta(account.frequency, ["brief", "detail"])   → 验证（冗余！）
```

**影响**：每个子智能体浪费 3-5 轮验证调用。8 个子智能体共 ~20-40 轮。

**根因**：子智能体的 `_sub_agent.py` 效率守则已经写了"不要验证写入"，但模型仍然执行验证。这是 LLM 行为问题，可能需要更强的提示约束。

**建议修复**：

**(a) 更强的提示约束**：
```
## 绝对禁止
- update_meta 返回成功后，禁止再调 meta 验证。返回值已包含写入内容。
- 违反此规则视为任务失败。
```

**(b) 在工具层面阻止**：update_meta 返回后的 2 轮内，如果 meta 读取 brief/detail 则返回"已写入，无需验证"。

### 问题 4：`meta(all=true)` 冗余读取 [P1]

**现象**：子智能体对每个列调用 `meta(col, all=true)`，获取全部字段（包括 file_size, created_at, modified_at 等无关信息）。

```
# 每个子智能体对每列都做：
L143  meta("account.account_id.INT.col", all=true)
      → 返回: cardinality, created_at, file, file_size, max_value,
              mean_value, min_value, modified_at, null_count, ...
```

**实际上子智能体只需要**：`cardinality`、`sample`、`topk`（用于理解数据内容），以及 `min_value/max_value`（数值列）或 `min_length/max_length`（文本列）。`file_size`、`created_at`、`modified_at` 完全无用。

**影响**：
- 每次返回大量无关信息，浪费 token
- 子智能体需要从大量字段中筛选有用信息

**建议修复**：

**(a) 主智能体在 task 中直接传入列的关键统计信息**：
```
已知 account_id 列: cardinality=4477, min=1, max=11382, null=0%
已知 frequency 列: cardinality=3, sample=["POPLATEK MESICNE", ...], null=0%
```
这样子智能体完全不需要读 meta。

**(b) 在 prompt 中强调精准读取**：
```
优先使用 meta(ref, property: ["cardinality", "sample", "topk"])
避免使用 meta(all=true)，除非确实需要全量信息
```

### 问题 5：子智能体失败后主智能体无差异重做 [P1]

**现象**：子智能体返回 `max_rounds_reached` 后，主智能体不知道哪些工作已完成，从零重做。

```
# district 子智能体失败后（L606）：
L606  meta(district.table, brief)         → 未找到（但子智能体已写过！）
L608  update_meta(district.table, ...)    → 覆盖子智能体已写入的内容
L612  meta(district.district_id, brief)   → 未找到（子智能体已写过！）
```

等等——子智能体的写入确实没生效？看 L543-544（子智能体写入了 district.table 的 brief）和 L607（主智能体读到未找到）。这说明**子智能体和主智能体的 store 操作是隔离的**？不对，子智能体使用的是同一个 store。

再仔细看：子智能体在 L543 写入了 district.table 的 brief，但主智能体在 L607 读到"未找到"。这意味着子智能体在达到 max_rounds 后被截断，后续的 update_meta 没有实际执行？或者这是两个不同的子智能体实例？

实际上，主智能体在 L606 开始处理 district 的重做，它在检查 brief 是否存在时得到"未找到"——但子智能体在 L543 已经写入了。这说明**子智能体的写入被正确提交了**，主智能体的 meta 查询可能有缓存问题，或者子智能体只写入了部分列。

看 L543：子智能体确实写了 `district.table` 的 brief。但 L606-608 主智能体读到了"未找到"然后又重写了。这可能是主智能体在同一轮对话中看不到子智能体的写入？

**影响**：
- 已完成的 brief/detail 被覆盖（虽然内容相似，但浪费轮次）
- 主智能体重复读取已知的 meta 信息

**建议修复**：

**(a) 子智能体 JSON 报告中包含已完成列表**：
```json
{
  "status": "max_rounds_reached",
  "completed_refs": ["district.table", "district.district_id", "district.A2", ...],
  "pending_refs": ["district.A9", "district.A10", ...]
}
```
主智能体根据 completed_refs 跳过已完成的工作。

**(b) 子智能体在被截断前 flush 结果**：在 `AgentExecutor` 中，当 `rounds >= max_rounds` 时，强制执行一次最终的 update_meta 调用。

### 问题 6：无批量 update_meta [P2]

**现象**：每列单独调用一次 `update_meta`。16 列的表需要 16 次调用。

```
L543  update_meta(district.table, ...)
L548  update_meta(district.district_id, ...)
L551  update_meta(district.A2, ...)
L556  update_meta(district.A3, ...)
...  // 16 次调用
```

**影响**：57 列 × 1 次调用 = 57 轮 update_meta。如果有批量接口，可以减少到 ~5 轮。

**建议修复**：

新增 `batch_update_meta` 工具：
```python
def batch_update_meta(store, updates: list[dict]) -> str:
    """批量更新多个实体的 meta。
    updates: [{"ref": "...", "fields": {...}}, ...]
    """
```

但这需要修改工具定义和注册，优先级较低。更好的做法是通过 prompt 让子智能体把同类型的调用连续排在一起，减少中间穿插的读取。

---

## 三、效率量化分析

### 总轮次统计

| 阶段 | 轮次 | 占比 | 可优化 |
|------|------|------|--------|
| 主智能体探索（关系发现） | ~35 | 15% | 少量 |
| 子智能体有效工作 | ~80 | 35% | - |
| 子智能体重试/验证 | ~40 | 17% | 100% |
| 主智能体重做子智能体工作 | ~40 | 17% | 100% |
| CSV/文件总结 | ~25 | 11% | 少量 |
| `::` 遍历失败重试 | ~16 | 5% | 100% |
| **总计** | **~236** | | **~40% 可消除** |

### 消除冗余后的预估

如果修复问题 1-5：
- `::` 遍历修复：-16 轮
- 子智能体不验证：-20 轮
- 子智能体够用（不重做）：-40 轮
- 主智能体 task 中传入列信息：-30 轮

预估总轮次：~130 轮，减少 ~45%。
按每轮 ~5-8 秒（含 API 调用），可从 34 分钟降至 ~18 分钟。

---

## 四、关系发现质量评估

Agent 创建了 6 个 `.rel` 实体：

| rel | 质量 | 评价 |
|-----|------|------|
| trans.bank ↔ order.bank_to | ✓ 好 | 两个字母银行代码，确实共享编码体系 |
| trans.account ↔ order.account_to | ✓ 好 | 外部银行账号，证据充分 |
| trans.k_symbol ↔ order.k_symbol | ✓ 好 | 交易分类符号，有 SIPO 重叠 |
| loan.payments ↔ order.amount | △ 一般 | 纯数值重叠，无明确语义关联 |
| loan.date ↔ account.date | △ 一般 | 日期类型的弱关联 |
| client.district_id ↔ account.district_id | ✓ 好 | 明确的 FK 引用同一维度表 |

**缺失的关系**：
- `disp.client_id` ↔ `client.client_id`（已有 FK，但 AI 未创建 .rel）
- `disp.account_id` ↔ `account.account_id`（已有 FK，但 AI 未创建 .rel）
- `card.disp_id` ↔ `disp.disp_id`（已有 FK，但 AI 未创建 .rel）

AI 过于关注 overlap 线索，忽略了从 FK 和业务逻辑出发发现关系。FK 实体已经存在（L153-154 列出了 7 个 FK），但 AI 没有将其转化为 .rel。

---

## 五、优先修复建议

### 立即修复（影响大、改动小）

1. **`::` 遍历文档化**：在 `_base.py` 工具使用策略中明确推荐路径 glob 模式（`db::table.*.*.col`），标注 `::` 遍历当前不支持列查找。

2. **子智能体 task 传入列统计**：主智能体在 task 中传入每列的 cardinality/sample/topk，子智能体不需要再调 meta。

3. **禁止写后验证**：在子智能体 prompt 中加入更强的约束。

### 短期修复（需要一些改动）

4. **动态 max_rounds**：主智能体根据列数计算 `max_rounds = column_count * 2 + 5`，传入 agent 工具。

5. **子智能体报告已完成 ref 列表**：在 AgentExecutor 中跟踪已成功 update_meta 的 ref，在 JSON 报告中返回。

6. **FK → .rel 转化提示**：在 coordinator prompt 中明确要求"已有的 .fk 实体也应该转化为 .rel 实体"。

### 长期优化

7. **批量 update_meta 工具**：支持一次调用更新多个实体。
8. **修复 `::` 遍历**：确保 table ↔ col 的边在静态提取阶段被创建。
