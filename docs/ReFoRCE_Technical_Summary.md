# ReFoRCE 技术方案总结

> 本文档详细梳理 ReFoRCE（Retrieval-Augmented Fine-grained Schema Linking for Complex Environments）的完整技术方案，包括表分组算法、Agent 推理流程、Schema 压缩策略，以及所有辅助技术，方便迁移到其他 Data Agent 系统。

---

## 一、整体架构概览

ReFoRCE 的完整流水线分为五个阶段：

```
┌──────────────┐    ┌───────────────┐    ┌────────────────┐    ┌──────────────┐    ┌──────────┐
│  1. 数据准备  │ -> │  2. DDL 压缩   │ -> │  3. Schema     │ -> │  4. Agent    │ -> │ 5. 评估   │
│  Setup       │    │  & 表分组      │    │  Linking       │    │  推理        │    │ & 投票    │
└──────────────┘    └───────────────┘    └────────────────┘    └──────────────┘    └──────────┘
```

**涉及文件：**
| 阶段 | 主要文件 |
|------|---------|
| 数据准备 | `spider_agent_setup_lite.py`, `spider_agent_setup_snow.py` |
| DDL 压缩 & 表分组 | `reconstruct_data.py` |
| Schema Linking | `schema_linking.py` |
| Agent 推理 | `agent.py`, `run.py`, `prompt.py`, `chat.py`, `sql.py` |
| 评估 & 投票 | `eval.py`, `agent.py` (vote_result) |

---

## 二、表分组算法（Digit-Based Table Deduplication）

### 2.1 问题背景

在 Snowflake、BigQuery 等大型数据仓库中，经常出现按数字后缀分区的表，例如：
- `events_2020`, `events_2021`, ..., `events_2024`（按年份分区）
- `orders_01`, `orders_02`, ..., `orders_12`（按月份分区）

这些表结构完全相同，全部放入 prompt 会造成巨大的 token 浪费。

### 2.2 核心算法

**位置：** `reconstruct_data.py` → `process_ddl()` 函数

**步骤：**

#### Step 1：清理不存在的表
遍历 DDL 中的所有表，若某个表没有对应的 `.json` 元数据文件，则从 DataFrame 中移除。

#### Step 2：按去数字表名分组
```
对每个表名 table_name:
    key = remove_digits(table_name)  # 用正则去掉所有数字字符
    将 table_name 加入 representatives[key] 列表
```

**`remove_digits()` 实现**（`utils.py`）：
```python
def remove_digits(s):
    return re.sub(r'\d', '', s)
```

**示例：** `events_2020` → `events_`，`events_2021` → `events_`，它们会被归入同一组。

#### Step 3：决定保留或合并
```
对每个组:
    if 组成员数 > 10:
        只保留第一个表（代表表），删除其余表
    else:
        删除该组的 key（不进行合并，所有表独立保留）
```

**阈值设计的原因：** 大于 10 个成员的组很可能是系统化分区表（如按月/按年），用代表表替代不会丢失信息；小于等于 10 的组可能是功能不同但碰巧名字相似的表，不应合并。

#### Step 4：校验 Schema 一致性
对被合并的表，验证代表表和被合并表的列名集合是否完全一致：
```python
def is_same_schema(repre_js_pth, js_pth):
    repre = json.load(f)
    js = json.load(f)
    return set(repre["column_names"]) == set(js["column_names"])
```

如果不一致，打印警告（但不阻止合并）。

#### Step 5：在 Prompt 中标注相似表
对于代表表，在 prompt 末尾附加：
```
Some other tables have the similar structure: ['events_2020', 'events_2021', ..., 'events_2024']
```
这样 LLM 知道可以通过 UNION ALL 或通配符查询这些表。

### 2.3 三个变体

| 函数 | 用途 | 区别 |
|------|------|------|
| `process_ddl()` | 标准分组 | 对全量表进行分组 |
| `process_ddl_gold()` | 使用标注表 | 先过滤只保留 gold 表，再分组 |
| `process_ddl_gold_schema()` | 使用 gold SQL 提取 | 先从 gold SQL 中提取表名和列名，用表名+去数字表名双重匹配过滤，再分组 |

### 2.4 迁移建议

将此算法迁移到其他 Data Agent 时：
1. 实现一个 `remove_digits()` 函数（核心只有一行正则）
2. 在构建 schema prompt 之前调用分组逻辑
3. 设置合理的阈值（当前为 10），可根据目标数据仓库特点调整
4. 在 prompt 中附带相似表列表，让 LLM 了解完整的表分布

---

## 三、DDL 压缩 & Prompt 构建流程

**位置：** `reconstruct_data.py` → `compress_ddl()` 函数

### 3.1 完整的 Prompt 结构

最终生成的 `prompts.txt` 由以下部分组成：

```
┌─────────────────────────────────────┐
│ 1. 表信息块（每个表重复以下结构）      │
│    - Table full name: db.schema.tbl │
│    - Column name + Type + Description│
│    - Sample rows (可选)              │
│    - Similar tables 标注 (如分组)     │
│    ─────────────────────────────     │
│ 2. ... 更多表 ...                    │
│    ─────────────────────────────     │
├─────────────────────────────────────┤
│ 3. External knowledge (外部知识文档)  │
├─────────────────────────────────────┤
│ 4. Table structure summary           │
│    {database: {schema: [tables]}}    │
└─────────────────────────────────────┘
```

### 3.2 压缩策略

| 策略 | 说明 | 条件 |
|------|------|------|
| **表去重分组** | 上节描述的 digit-based 分组 | `--rm_digits` 启用 |
| **列级裁剪** | Schema Linking 后只保留相关列 | `--reduce_col` 启用 |
| **描述清除** | 清除所有列描述 | prompt > 200KB 且 `--clear_long_eg_des` |
| **Sample rows 截断** | 长字符串值截断到 1000 字节 | 始终生效 |
| **表名截断** | Snowflake 表名只保留最后一部分 | sf 开头的实例 |

### 3.3 多引擎适配

Prompt 构建根据数据库引擎类型（Snowflake / BigQuery / SQLite）自动适配：
- **表名引用**：Snowflake 用双引号 `"COL"`，BigQuery 用反引号 `` `col` ``
- **嵌套列处理**：Snowflake 用 `LATERAL FLATTEN`，BigQuery 用 `JSON_EXTRACT_SCALAR + UNNEST`，SQLite 用 `json_extract + json_each`
- **模糊匹配**：Snowflake 用 `ILIKE`，BigQuery 用 `LOWER() + LIKE`
- **多表 UNION**：Snowflake 显式列出所有表名，BigQuery 支持通配符表 `table_prefix*`

---

## 四、Schema Linking（表级 & 列级筛选）

**位置：** `schema_linking.py`

### 4.1 触发条件

当 `prompts.txt` 超过阈值（默认 200,000 字节）时，启动 Schema Linking。

### 4.2 表级 Schema Linking

对每个表，使用 LLM（GPT-4o）判断该表是否与当前任务相关：

**LLM 输入：**
```
You are doing table level schema linking. Given a table with schema information
and the task, you should think step by step and decide whether this table is
related to the task. You should answer Y/N only. If the answer is Y, you should
add columns that you think is related in python list format.
```

**LLM 输出格式：**
```json
{
  "think": "逐步推理过程...",
  "answer": "Y 或 N",
  "columns": ["col1", "col2"]  // 仅当 Y 时
}
```

- 每个表独立调用 LLM，最多重试 3 次
- 使用线程池并行处理（32 workers）

### 4.3 DDL 裁剪

根据 Schema Linking 结果：
1. 生成 `DDL_sl.csv`，只包含被判定为 Y 的表
2. 若启用 `--reduce_col`，则同时裁剪列，只保留 LLM 认为相关的列
3. 使用 `compress_ddl()` 重新生成精简后的 `prompts.txt`

### 4.4 列级裁剪实现

```python
def reduce_columns(sql, subset_columns):
    # 解析 CREATE TABLE DDL
    # 只保留 subset_columns 中的列
    # 重建精简的 CREATE TABLE 语句
```

---

## 五、Agent 推理流程

**位置：** `agent.py` → `REFORCE` 类

### 5.1 整体流程图

```
                    ┌─────────────┐
                    │ 开始推理     │
                    └──────┬──────┘
                           │
              ┌────────────▼────────────┐
              │ Format Restriction (可选)│  确定期望的 CSV 输出格式
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │ Column Exploration (可选)│  生成探索 SQL，理解数据
              └────────────┬────────────┘
                           │
         ┌─────────────────▼─────────────────┐
         │     Self-Refinement 迭代循环       │
         │  ┌──────────────────────────────┐  │
         │  │ 生成 SQL → 执行 → 检查结果    │  │
         │  │   ↓ 成功    ↓ 失败           │  │
         │  │ Self-Consistency  Self-Correct│  │
         │  │   ↓ 一致    ↓ 修正后重试      │  │
         │  │   结束      继续迭代          │  │
         │  └──────────────────────────────┘  │
         │  最多 max_iter 轮 (默认 5)         │
         └─────────────────┬─────────────────┘
                           │
              ┌────────────▼────────────┐
              │ Majority Voting (可选)   │  N 次独立运行，投票选最优
              └────────────┬────────────┘
                           │
                    ┌──────▼──────┐
                    │ 最终结果     │
                    └─────────────┘
```

### 5.2 阶段详解

#### Phase 1: Format Restriction（格式约束）

**目的：** 在生成 SQL 之前，先确定期望的 CSV 输出格式（列名和类型）。

**方法：** 使用单独的 LLM 调用，让模型根据问题生成一个 CSV header 格式：
```
product_name:str, product_id:int
```

**迁移价值：** 通过预先约束输出格式，减少 LLM 生成不正确列名/列数的概率。

---

#### Phase 2: Column Exploration（列探索）

**目的：** 在正式生成答案 SQL 之前，先用简单 SQL 探索数据库中相关列的实际值。

**流程：**
1. LLM 生成至多 10 个简单 SELECT 查询（带 `--Description:` 注释）
2. 逐个执行每个 SQL，遇到错误则 Self-Correct（最多 3 次重试）
3. 将成功的 SQL + 结果作为 few-shot examples 附加到后续 prompt 中

**关键设计：**
- 要求至少生成 3 个 SQL，否则重试
- 累积结果不能超过 100,000 字符，否则重试
- 成功执行的 SQL 数为 0 则放弃该样本
- 执行结果超过 10 个或对话超过 20 条消息时停止

**错误传播机制：** 当一个 SQL 的错误被修正后，将修正信息传递给后续未执行的 SQL，让 LLM 批量修正可能存在的类似错误：
```
"sql1 is corrected to sql1'. Please correct other sqls based on results if they have similar errors."
```

**空结果简化机制：** 如果 SQL 执行成功但返回空结果，触发 `simplify=True`，提示 LLM 简化查询条件。

**迁移价值：** 这是 ReFoRCE 最核心的策略之一。通过预探索，Agent 对数据值有了直觉认识，后续生成 SQL 时能更准确地选择过滤条件、JOIN 键和聚合函数。

---

#### Phase 3: Self-Refinement（自我修正迭代）

**目的：** 迭代式地生成、执行、修正 SQL，直到获得满意结果。

**流程：**
```
循环最多 max_iter (5) 次:
    1. LLM 生成 1 个 SQL
    2. 执行 SQL
    3a. 若执行成功:
        - 若未启用 Self-Consistency: 直接结束
        - 若启用 Self-Consistency:
            - 检查结果是否与之前某次一致
            - 若一致: 结束（Self-Consistency 达成）
            - 若不一致: 继续迭代
    3b. 若执行失败:
        - 将错误信息反馈给 LLM
        - 要求修正并输出 1 个完整 SQL
        - 继续下一次迭代
```

**Self-Consistency 判定逻辑：**
1. 读取 CSV 结果，float 列四舍五入到 2 位小数
2. 取第一列所有值作为指纹
3. 若指纹与历史结果匹配，认为达成一致性，终止迭代

**结果验证：**
- 嵌套值检测：如 `[\nA,\n B\n]` 形式的值 → 提示 LLM 展平
- 全零/全空列检测 → 提示 LLM 修正
- 三引号检测 → 提示 LLM 使用 CAST 替代

**Early Stopping：** 若连续 4 次结果都是空结果，提前终止并移除输出文件。

**Condition-Omit 检测：** 如果 SQL 中出现 `-- Include all`、`-- Omit`、`-- ...` 等注释（表明 LLM 试图省略 UNION 中的某些表），则注入方言特定的 UNION 指导：
- Snowflake：显式列出所有表名
- BigQuery：使用通配符表

---

#### Phase 4: Majority Voting（多数投票）

**目的：** 通过多次独立运行并投票，提高最终答案的可靠性。

**流程：**
1. 启动 N 个独立线程（默认 8），每个运行完整的 Self-Refinement 流程
2. 收集所有成功的结果
3. 两两比较结果（使用 `compare_pandas_table`，忽略顺序）
4. 构建等价类，统计每个等价类的票数
5. 选择票数最高的结果

**平局处理：**

| 策略 | 说明 |
|------|------|
| `random_vote_for_tie` | 随机选择票数最高中的一个 |
| `model_vote` | 使用 LLM 推理选择最佳 SQL |
| `final_choose` | 直接选择第一个结果 |

**LLM 投票（model_vote）的推理指令：**
```
1. Exclude unreasonable results
2. Check results if aligning with task description
3. Analyze SQL if aligning with task description
For results with null or zero values, they tend to be wrong answer.
```

---

## 六、完整执行流水线（run_main_gen_sl.sh）

实际运行脚本展示了一个多阶段的级联策略：

```
Stage 1: 数据准备 + DDL 压缩
    spider_agent_setup → reconstruct_data --rm_digits

Stage 2: Schema Linking（对大 schema 进行表级筛选）
    schema_linking --linking_method gen --clear_long_eg_des

Stage 3: 第一轮 Self-Refinement + 投票（不包含列探索）
    run.py --do_self_refinement --do_vote --num_votes 8

Stage 4: 第二轮 Self-Refinement + 投票（加入列探索，只处理失败样本）
    run.py --do_self_refinement --do_column_exploration --rerun --overwrite_unfinished

Stage 5: 随机投票处理平局
    run.py --do_vote --random_vote_for_tie

Stage 6: 最终选择
    run.py --do_vote --random_vote_for_tie --final_choose

Stage 7: 评估
    eval.py → get_metadata.py → evaluate.py
```

**关键设计：** 这是一个渐进式的级联策略。先用简单方法处理简单样本，对失败的样本逐步加入更强的策略（列探索、重新投票等），平衡了成本和效果。

---

## 七、所有策略清单

### 7.1 Schema 压缩类

| # | 策略 | 核心思想 |
|---|------|---------|
| 1 | **Digit-Based 表分组** | 去掉表名中的数字，同名表合并为代表表 |
| 2 | **Schema Linking** | LLM 逐表判断相关性，过滤无关表 |
| 3 | **列级裁剪** | 只保留 Schema Linking 认为相关的列 |
| 4 | **描述清除** | prompt 过长时移除所有列描述 |
| 5 | **Sample Rows 截断** | 长字符串值截断到 1000 字节 |

### 7.2 Agent 推理类

| # | 策略 | 核心思想 |
|---|------|---------|
| 6 | **Column Exploration** | 预探索：生成多个简单 SQL 理解数据值分布 |
| 7 | **Self-Correction** | 执行失败时自动修正 SQL（最多 3 次重试） |
| 8 | **Error Propagation** | 修正一个 SQL 后，批量修正其他可能类似的 SQL |
| 9 | **Empty Result Simplification** | 空结果时提示 LLM 简化查询条件 |
| 10 | **Self-Refinement** | 迭代生成-执行-修正循环（最多 5 轮） |
| 11 | **Self-Consistency** | 当两次迭代结果一致时提前终止 |
| 12 | **Early Stopping** | 连续 4 次空结果时终止 |
| 13 | **Result Validation** | 检测嵌套值、全零列、三引号等异常结果 |

### 7.3 投票 & 选择类

| # | 策略 | 核心思想 |
|---|------|---------|
| 14 | **Majority Voting** | N 次独立运行，选择出现次数最多的结果 |
| 15 | **LLM-Based Voting** | 平局时用 LLM 推理选择最佳 SQL |
| 16 | **Format Restriction** | 预先约束输出 CSV 格式 |
| 17 | **Progressive Cascade** | 渐进式级联：先用简单策略，失败后逐步加码 |

### 7.4 Prompt 工程类

| # | 策略 | 核心思想 |
|---|------|---------|
| 18 | **方言特定 Prompting** | 根据引擎类型注入不同的 SQL 语法示例 |
| 19 | **Condition-Omit 检测** | 检测 LLM 省略 UNION 的意图，注入完整表名列表 |
| 20 | **Nested Column 指导** | 针对不同引擎提供 JSON 嵌套列的查询方法 |
| 21 | **Decimal Place 指令** | 统一保留四位小数 |
| 22 | **Exploration Few-Shot** | 将探索阶段的结果作为 few-shot examples 注入 prompt |

### 7.5 工程保障类

| # | 策略 | 核心思想 |
|---|------|---------|
| 23 | **并行执行** | ThreadPoolExecutor 并行处理多个样本 |
| 24 | **超时保护** | SQLite 查询 300 秒超时 |
| 25 | **多 Provider 支持** | 兼容 OpenAI / Azure OpenAI / DeepSeek |
| 26 | **长度预算** | CSV 结果截断到 500 字符、prompt 预算控制 |

---

## 八、迁移到其他 Data Agent 的建议

### 最小可行迁移（必选）

1. **Digit-Based 表分组**：实现 `remove_digits()` 和 `process_ddl()`，阈值可调
2. **DDL 到 Prompt 转换**：将 schema 元数据转为结构化文本 prompt
3. **Self-Correction 循环**：执行失败时将错误信息反馈 LLM 重新生成

### 推荐迁移（高性价比）

4. **Column Exploration**：预探索策略显著提升 SQL 准确率
5. **Self-Consistency**：结果指纹比对，一致性达成即停止
6. **Schema Linking**：对大 schema 必不可少

### 可选迁移

7. **Majority Voting**：需要多次调用的成本，但能显著提高可靠性
8. **Format Restriction**：减少输出格式错误
9. **Progressive Cascade**：渐进式策略，平衡成本和效果
