# Agent 工具调用分析与优化建议

基于 `california_schools` 全量提取（debug 模式）的日志分析。
测试环境：3 张表、89 列、6 个 overlap、2 个 FK，agent 共消耗 ~35 轮工具调用。

---

## 问题一：重复工作 — 子智能体和主 Agent 做了同样的事

### 现象

主 Agent 派出子智能体为 3 个 CSV 描述文件写 brief/detail。子智能体完成了工作，但 15 轮耗尽返回 `[max rounds reached]`。主 Agent 随后**自己又重新读了一遍 CSV 文件并再次 update_meta**，覆盖了子智能体已经写好的内容。

日志时间线：

```
22:57:08  主 Agent 派出子智能体处理 3 个 CSV
22:58:00  子智能体 update_meta schools.csv ✓
22:58:08  子智能体 update_meta frpm.csv ✓
22:58:16  子智能体 update_meta satscores.csv ✓
22:58:20  子智能体开始验证（6 次 meta 调用读取写回的结果）
22:58:39  子智能体 [max rounds reached]，返回给主 Agent
22:58:47  主 Agent 自己又 update_meta schools.csv（覆盖）
22:58:53  主 Agent 自己又 update_meta frpm.csv（覆盖）
22:58:59  主 Agent 自己又 update_meta satscores.csv（覆盖）
```

### 根因

- 子智能体以 `[max rounds reached]` 结束而非正常结束，主 Agent 无法判断子智能体是否完成任务
- 主 Agent 看到返回的不是确认信息而是截断文本，认为任务未完成，于是自己做了一遍
- 主 Agent 在派子智能体之前**已经读过了这些 CSV 文件**（22:56:37-22:56:50），子智能体又重新读了一遍

### 解决方案

**方案 A：让子智能体返回结构化的完成报告**

子智能体工具的返回值目前只是 LLM 的文本输出。可以改为返回结构化信息：

```python
# tool_use/sub_agent/tool.py — AgentExecutor
def __call__(self, store, arguments):
    # ... 现有逻辑 ...
    return json.dumps({
        "status": "completed" | "max_rounds_reached",
        "actions_taken": [...],  # 工具调用摘要
        "result": agent.last_message,
    })
```

主 Agent 看到明确的状态，就知道是否需要补做。

**方案 B：在 prompt 中明确「已完成的任务不要重做」**

在 coordinator prompt 中增加规则：

```
- 子智能体返回后，检查它是否已完成任务。如果子智能体返回的内容表明工作已完成（即使带 max rounds 截断），不要重复执行。
```

---

## 问题二：过度验证 — 每次写入后都读回来确认

### 现象

Agent 在每次 `update_meta` 后都会调用 `meta` 读回来验证。对于数据库文件的 brief+detail，甚至验证了两次（分别读 brief 和 detail）。

```
22:56:10  update_meta california_schools.db → brief, detail
22:56:14  meta california_schools.db property=brief    ← 验证
22:56:18  meta california_schools.db property=detail   ← 验证
```

satscores 子智能体也做了同样的事：

```
22:52:12  update_meta satscores.table → brief, detail
22:52:16  meta satscores.table                        ← 验证
```

3 个 CSV 文件 × (update_meta + verify brief + verify detail) = 9 次调用，其中 6 次是纯验证。

### 根因

- Agent 不信任 `update_meta` 的返回值。当前 `update_meta` 返回 `"Updated xxx: brief, detail"`，但没有展示实际写入的内容
- LLM 的行为倾向：写完东西后想确认效果，这是模型的固有倾向
- Prompt 中没有约束这个行为

### 影响

在 california_schools 这个 3 表小库上，验证调用约占总轮次的 20%。在更大的数据库上（十几张表），如果每张表都验证，可能浪费 30-40 轮。

### 解决方案

**方案 A：增强 update_meta 返回值**

让 `update_meta` 返回实际写入的内容，这样 Agent 就不需要额外调 meta 验证：

```python
# 当前
return f"Updated {ref}: {', '.join(fields.keys())}"

# 改进后
return f"Updated {ref}:\n" + "\n".join(f"  {k}: {v}" for k, v in fields.items())
```

**方案 B：在 prompt 中加入规则**

```
- update_meta 和 create_entity 成功后不要调用 meta 验证。工具的返回值已经确认了操作结果。
- 如果确实需要确认，只在最终交付前做一次批量检查，不要每次写入后都验证。
```

**建议：A + B 同时实施。** A 从工具层面减少 Agent 的不确定感，B 从 prompt 层面明确行为约束。

---

## 问题三：缺少 SQL 查询工具

### 现象

Agent 想验证 "frpm 表的 School Code 列是否是 CDSCode 的后7位"，无法用现有工具完成，被迫用 bash 跑 python+sqlite3：

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('california_schools.db')
cursor = conn.cursor()
cursor.execute('SELECT CDSCode, \"School Code\" FROM frpm LIMIT 20')
rows = cursor.fetchall()
for cds, sc in rows:
    if sc is None or cds is None:
        continue
    if not cds.endswith(sc):
        print(f'Mismatch: CDSCode={cds}, School Code={sc}')
..."
```

这消耗了 3 轮工具调用：
1. `bash("head -20 california_schools.db | file -")` — 确认文件类型
2. `bash("python3 --version")` — 确认 python 可用
3. `bash("python3 -c ...")` — 实际执行 SQL

### 根因

当前工具集面向的是"读取已有的知识图谱信息"（meta/glob/read/grep/lookup/search），缺少对原始数据源的**临时查询**能力。Agent 想做任意 SQL 查询时只能绕路用 bash。

### 影响

- bash 执行 SQL 是不安全的（SQL 注入风险、文件路径问题）
- Agent 需要花额外轮次确认环境和构造代码
- 返回结果的格式不可控，Agent 可能需要额外解析

### 解决方案

**新增 `query` 工具**

```python
# tool_use/query/tool.py
def query_command(store, arguments):
    """执行 SQL 查询并返回结果。"""
    db_ref = arguments["db"]         # 数据库 ref，如 "event.db"
    sql = arguments["sql"]           # SQL 语句
    limit = arguments.get("limit", 50)

    db_meta = store.get_meta(db_ref)
    db_path = os.path.join(store.project_path, db_meta["path"])

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchmany(limit)
        # 格式化输出
        ...
    finally:
        conn.close()
```

Schema:

```json
{
  "name": "query",
  "parameters": {
    "db": "数据库 ref，如 'california_schools.db'",
    "sql": "SELECT 语句（只读，禁止 INSERT/UPDATE/DELETE）",
    "limit": "返回行数上限，默认 50"
  }
}
```

安全措施：
- 只允许 SELECT，通过正则或 SQL 解析拒绝写入语句
- 设置 limit 上限（如 200），防止返回过多数据
- 设置查询超时（如 10 秒）

---

## 问题四：glob 找 overlap/rel 实体效率低

### 现象

Agent 想找与 School Code 相关的 overlap 实体：

```
glob("*School Code*overlap")       → No objects found
glob("*cname*overlap")             → No objects found
```

因为 overlap 实体的命名格式是 `{from_table}.{from_col}__to__{to_table}.{to_col}.overlap`，Agent 不知道这个命名规则，用通配符碰运气。

类似地，Agent 想找列的 overlap 关系但不知道怎么构造 pattern，经常需要 2-3 次尝试。

### 根因

- overlap/rel 实体的命名规则没有在任何工具文档或 prompt 中说明
- glob 是纯路径匹配，不理解实体间的关联语义
- Agent 想问"这列有哪些关联"，但 glob 只能回答"哪些路径匹配这个 pattern"

### 解决方案

**方案 A：在 prompt 中明确命名规则**

在 coordinator prompt 中补充：

```
实体命名规则：
- overlap: [数据库]::[表1].[列1]__to__[表2].[列2].overlap
- rel:     [数据库]::[表1].[列1]__rel__[表2].[列2].rel
- fk:      [数据库]::[表1].[列1]__to__[表2].[列2].fk

查找某列的所有关联：
  glob "california_schools.db::*CDSCode*overlap"   ← 用列名做通配
  glob "california_schools.db::*CDSCode*.rel"
```

**方案 B：新增 `relations` 工具（推荐）**

直接回答"某个实体有哪些关联关系"，而不是让 Agent 用 glob 猜 pattern：

```python
def relations_command(store, arguments):
    """查看与指定实体相关的所有关系。"""
    ref = arguments["ref"]  # 如 "california_schools.db::schools.CDSCode.TEXT.col"

    results = []
    # 查找以该节点为端点的所有边
    for edge in store.get_edges(ref):
        other = edge["b"] if edge["a"] == ref else edge["a"]
        other_meta = store.get_meta(other)
        results.append({
            "entity": other,
            "type": other_meta.get("relation_type", "unknown"),
            "brief": other_meta.get("brief", ""),
        })

    return format_results(results)
```

这比让 Agent 多次 glob + meta 高效得多。

---

## 问题五：meta 工具无法模糊匹配实体类型

### 现象

Agent 想查 `frpm.District Code` 列的信息，直接用了：

```
meta("california_schools.db::frpm.District Code.TEXT.col")
→ No metadata found
```

实际实体是 `frpm.District Code.INT.col`（INT 而非 TEXT）。Agent 不得不额外用 glob 找到正确的 ref：

```
glob("*District Code*")
→ california_schools.db::frpm.District Code.INT.col
```

### 根因

- Agent 不知道列的数据类型（除非先 glob 查看）
- meta 工具要求精确的 ref，不支持模糊匹配
- glob 能模糊搜索但 meta 不能

### 解决方案

**方案 A：meta 支持 ref 补全**

当传入的 ref 不完全匹配时，尝试前缀匹配并返回建议：

```python
def meta_command(store, path, **kwargs):
    # 精确匹配
    if store.node_exists(path):
        return store.get_meta(path)

    # 前缀匹配：尝试补全类型后缀
    candidates = store.find_nodes(f"{path}.*")
    if len(candidates) == 1:
        return store.get_meta(candidates[0])
    elif len(candidates) > 1:
        return f"Ambiguous ref '{path}', candidates:\n" + "\n".join(candidates)

    return f"No metadata found for '{path}'"
```

**方案 B：glob 返回结果中包含完整 ref**

确保 glob 的每一行都包含可被 meta 直接使用的完整 ref。目前 glob 输出格式是：

```
california_schools.db::frpm.District Code.INT.col | Distinct: 1018, ...
```

agent 需要提取 `|` 前面的部分传给 meta。可以在 prompt 中强调这一点，或者让 glob 输出更结构化。

---

## 问题六：子智能体轮次分配不合理

### 现象

为 3 个 CSV 文件写 brief/detail 的子智能体，15 轮全部耗尽。拆解其调用链：

```
read schools.csv limit=10          (1)
read schools.csv offset=10 limit=10 (2)  ← 为什么不一次读完？
read frpm.csv limit=10             (3)
read frpm.csv offset=10 limit=10   (4)
read satscores.csv limit=15        (5)
meta schools.csv                   (6)  ← 主 Agent 已在 task 中给了行数信息
update_meta schools.csv            (7)
update_meta frpm.csv               (8)
update_meta satscores.csv          (9)
meta schools.csv property=brief    (10) ← 验证
meta schools.csv property=detail   (11) ← 验证
meta frpm.csv property=brief       (12) ← 验证
meta frpm.csv property=detail      (13) ← 验证
meta satscores.csv property=brief  (14) ← 验证
→ max rounds reached               (15)
```

有效操作 5-9 次（读文件+写结果），验证操作 10-14 次（5次），信息获取 6 次（冗余）。实际有效操作只占 1/3。

### 根因

- 子智能体 prompt 中没有告诉它"不要验证写入结果"
- 子智能体 read 的 limit=10，49 行的文件需要读两次。主 Agent 在 task 中已经描述了文件内容，子智能体仍然去重新读取
- 子智能体的默认 max_rounds=15 对"处理多个文件"的场景偏低

### 解决方案

**A. 子智能体 prompt 模板中加入约束**

在 `tool_use/sub_agent/prompt.py` 中加入通用规则：

```
## 效率守则
- 不要验证 update_meta / create_entity 的结果。工具返回成功即表示写入完成。
- 如果 task 中已经提供了足够的信息，不要重复读取原始数据。
- 优先批量操作，减少不必要的工具调用。
```

**B. 动态计算子智能体轮次**

根据 task 复杂度自动计算合理的轮次：

```python
def _estimate_rounds(task: str, num_files: int = 1) -> int:
    base = 5  # 基础轮次
    per_file = 3  # 每个文件额外轮次
    return base + num_file * per_file
```

或者让主 Agent 在调用时根据任务量选择合理的 max_rounds（目前 prompt 中已建议 8-10，但实际 agent 经常用默认值 15）。

---

## 优先级建议

| 优先级 | 问题 | 预计收益 | 实现难度 |
|---|---|---|---|
| P0 | 减少验证调用（问题二、六） | 减少 30-40% 轮次消耗 | 低（改 prompt + 增强返回值） |
| P1 | 新增 query 工具（问题三） | 消除 bash 绕路，提升分析能力 | 中 |
| P1 | 子智能体完成状态（问题一） | 避免重复工作 | 低 |
| P2 | 新增 relations 工具（问题四） | 简化关联查询 | 中 |
| P3 | meta 模糊匹配（问题五） | 减少试错调用 | 低 |
