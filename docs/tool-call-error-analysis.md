# Pontis 工具调用错误分析

本文总结 `workspace/baselines/pontis/preprocess_logs/bird_dev_bird_dev_full_extract_force_20260527` 中出现的 agent 工具调用错误，并判断这些错误是否来自工具设计违反模型直觉。

## 总体结论

这些错误不应全部归因于 agent 输出不稳定。其中一部分确实来自工具接口和引用语法不够贴近模型直觉，尤其集中在 `update_meta`、`meta` 多实体匹配和 ref 路径语法上。

优先级最高的问题是 `update_meta.fields` 的类型设计。工具要求 `fields` 必须是对象，例如：

```json
{"fields": {"hints": ["..."]}}
```

但模型很自然会把“给实体追加若干提示”表达成：

```json
{"fields": ["hint1", "hint2"]}
```

当前实现会直接触发 Python 内部异常：

```text
Tool error (update_meta): AttributeError: 'list' object has no attribute 'keys'
```

这属于工具鲁棒性和接口直觉问题，应该在工具层兼容或返回清晰错误。

## 错误类型

### 1. `update_meta.fields` 类型错误

典型日志：

```text
Tool error (update_meta): AttributeError: 'list' object has no attribute 'keys'
Tool error (update_meta): AttributeError: 'str' object has no attribute 'keys'
```

原因：

- `update_meta_command()` 假设 `fields` 一定是 dict，并直接调用 `fields.keys()`。
- 但模型常把 `fields` 写成 list 或 string，尤其是在只想追加 `hints` 时。
- 工具 schema 只声明 `fields` 是 object，没有进一步约束字段结构。

判断：

- 这是工具设计责任较大。
- “追加提示”是高频操作，list/string 是符合人类和模型直觉的输入。
- 工具不应暴露 Python 内部异常。

建议：

- `fields` 为 list/string 时自动转换为 `{"hints": fields}`。
- 其他非法类型返回明确错误，例如：
  `错误: fields 必须是对象；如果要追加 hints，可直接传字符串或字符串列表。`

### 2. ref 语法与 SQL/人类列名引用习惯冲突

典型日志：

```text
Error: 未找到匹配的实体: california_schools::california_schools.sqlite:db/frpm:table/[Academic Year]:col
```

原因：

- 模型把带空格的列名写成 `[Academic Year]`。
- 这符合 SQL Server 或一般“引用特殊列名”的习惯。
- 但 Pontis ref 语法中 `[]` 属于通配符字符集合，导致 resolver 进入通配匹配路径，无法命中真实列名。

判断：

- 这是工具语法和数据库列名直觉冲突。
- 对 Text2SQL 场景尤其明显，因为模型会把 SQL 引用习惯迁移到图谱 ref。

建议：

- resolver 对路径 segment 的外层 `[]` 做兼容剥离。
- 或至少在错误提示中说明：
  `ref 中列名不要加 []；请使用 /Academic Year:col。SQL 中再用双引号引用特殊列名。`

### 3. `meta` 不支持多实体，但模型会自然用通配

典型日志：

```text
meta({"ref": "debit_card_specializing::debit_card_specializing.sqlite:db/customers:table/*:col"})
Error: 匹配到多个实体
```

原因：

- `meta` 设计为查看单个实体。
- 模型想“查看某表所有列的元数据”，自然会用 `*:col`。
- 这类用法在 `find` 中合法，但在 `meta` 中不合法。

判断：

- 这不是严格 bug，但属于工具边界不够符合模型直觉。
- 当前错误提示只说“匹配到多个实体”，没有告诉模型下一步该怎么做。

建议：

- 保持 `meta` 单实体语义也可以。
- 但多匹配错误应引导：
  `meta 只接收唯一实体；若要列出多个实体，请先用 find，再逐个 meta。`
- 若后续需要提升效率，可新增 `meta_many` 或允许 `meta` 对少量匹配返回紧凑摘要。

### 4. find 输出不是 canonical ref，容易导致复制/拼接错误

Pontis 当前设计要求 `find` 输出严格继承输入 ref 的路径结构，不自动生成 canonical ref。这在 `Pontis/tool/README.md` 中是明确设计。

优点：

- 路径遍历模型统一。
- `meta` 的 Related 也可用 `主节点ref/邻接名称:标签` 自然访问。

问题：

- 对模型来说，`find` 第一列看起来像“可复制的完整 ref”，但它未必是全局 canonical ref。
- 对 `fk/rel/overlap` 这类关系实体，模型容易自行拼出不存在的路径，例如：
  `db/*:overlap/Examination.ID->Laboratory.ID:overlap`
- 最终导致 `meta` 找不到实体。

判断：

- 这是设计取舍，不是单点 bug。
- 但它会增加 LLM 对 ref 语法的学习成本。

建议：

- 保持路径模型时，应增强错误恢复提示。
- `find` 输出可以额外展示一个稳定写入 ref 字段，但不替代第一列路径语义。
- 或在 `meta`/`update_meta` 失败时返回候选相近实体。

### 5. `create_entity` 长文本 JSON 失败

典型日志：

```text
Tool argument parse error call#0(create_entity): invalid JSON arguments
```

原因：

- README 或长 detail 被一次性塞进 function call。
- 长字符串中包含换行、引号、特殊符号时，模型更容易输出 malformed JSON。

判断：

- 主要是模型输出稳定性问题，但接口形态会放大问题。
- 对长文档写入，`create_entity` 同时负责创建节点、写长 meta、建边，调用负担偏重。

建议：

- README 类任务优先使用两步：
  1. `create_entity` 创建空节点或短 meta。
  2. `update_meta` 写长 `detail`。
- 后续可提供更贴近任务的 `upsert_entity` 或 `write_readme`。

### 6. SQL 执行错误

典型日志：

```text
SQL 执行错误: OperationalError: near "OFFSET": syntax error
SQL 执行错误: OperationalError: no such column: superhero.id
SQL 执行错误: OperationalError: LIMIT clause should come after UNION ALL not before
```

原因：

- 模型写错 SQLite 语法。
- 模型引用了错误列名或 alias。
- 带空格列名未用双引号。

判断：

- 主要是 agent SQL 生成错误，不是工具设计错误。
- 但 query 工具可以改进错误提示，帮助模型更快自修正。

建议：

- 对 SQLite 常见错误增加 hint：
  - `OFFSET` 必须和 `LIMIT` 一起使用。
  - 特殊列名用双引号。
  - `UNION ALL` 中单个分支的 `LIMIT` 需要子查询包裹。
- 保留原始 SQLite 错误，避免过度解释。

## 优先改进顺序

1. **修复 `update_meta.fields` 类型鲁棒性**
   - 直接消除最多的工具异常。
   - 兼容 list/string 为 hints 快捷写法。

2. **改进 ref resolver 对外层 `[]` 的兼容**
   - 特别适合数据库列名含空格、括号、百分号的场景。

3. **增强多匹配/未匹配错误提示**
   - 对 `meta(*:col)` 明确引导用 `find`。
   - 对未找到列名给出附近候选。

4. **降低长文本 function-call 失败率**
   - README 写入流程改为短 create + 长 update。
   - 或新增专用 upsert/write 工具。

5. **为 SQL 错误添加 SQLite 定向 hint**
   - 属于低风险、提升恢复率的改进。

## 是否违反工具直觉

按责任划分：

| 类型 | 是否违反工具直觉 | 说明 |
|---|---:|---|
| `update_meta.fields` list/string 报内部异常 | 是 | 高频 hints 写入自然会这样表达 |
| ref 中 `[Column Name]` 找不到 | 是 | SQL 引用习惯与 ref 通配语法冲突 |
| `meta(*:col)` 多匹配报错 | 部分是 | 单实体设计合理，但错误提示不足 |
| find 输出不是 canonical ref | 部分是 | 统一路径模型合理，但复制稳定性弱 |
| `create_entity` 长 JSON parse error | 部分是 | 模型问题为主，接口负担偏重 |
| SQL 语法/列名错误 | 否 | 主要是 agent SQL 生成错误 |

## 结论

当前工具层最大问题不是“功能缺失”，而是错误恢复和输入容错不足。对 LLM agent 来说，工具 API 应该优先容忍高频自然写法，并在无法执行时给出可操作的下一步。否则模型会把轮次浪费在修正工具调用格式上，甚至在 explorer 阶段写入不完整元数据。

最小改动建议是先改 `update_meta` 和 ref 错误提示。这两项不会改变 Pontis 的图谱设计，但能显著降低 extract 阶段的工具调用噪声。
