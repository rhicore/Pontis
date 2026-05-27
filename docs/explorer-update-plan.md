# Explorer 更新总计划

本文档记录对 benchmark 错题做完数据库级分析后，准备沉淀到 explorer 层的通用改进。目标不是面向某个题集写规则，而是让图谱在只观察数据库 schema、值分布、关系和文件说明的前提下，给 solver 提供更稳定的实体选择提示。

## 设计原则

- explorer 只能写当前数据库中可验证的事实：表列语义、样例值、统计分布、外键、重叠率、行粒度、候选实体差异。
- 不写参考 SQL、gold 风格、数据集评测习惯，也不写某道题的专门答案。
- 优先把短提示写到基础实体的 `hints` 属性，让 agent 正常 `meta` 表/列时自动看到。
- 涉及多个实体的选择边界时，创建邻接 `hint` 或 `disambig` 实体，同时把一句短提示回写到相关表/列的 `hints`。
- hint 应该描述“何时使用这个实体、何时不要使用这个实体”，而不是简单重复 brief/detail。

## 当前暴露的问题类型

### 1. 字面输出字段与语义近似字段竞争

代表 case：`california_schools` Q80。

问题现象：

- 用户问题要求 `school type`、`school name`、`latitude`。
- 数据库中 `schools` 表有 `School`、`SOCType`、`Latitude`，语义上足以回答问题。
- 但 `frpm` 表有字面字段 ``School Name``、``School Type``，gold SQL 使用 `frpm` 的这两个输出字段，再通过 `CDSCode` join `schools.Latitude`。
- 当前 agent 只看 `schools` 后，选择了 `schools.SOCType` 和 `schools.School`，没有意识到 `frpm` 中存在更字面匹配的输出字段。

数据库事实：

- `schools.SOCType` 是运营类型说明，对应 `SOC` code。
- `frpm.`School Type`` 是 FRPM 记录中的学校类型展示字段。
- `schools.School` 是学校主表官方名。
- `frpm.`School Name`` 是 FRPM 记录中的学校名展示字段。
- 两表通过 `CDSCode` 关联，`schools.Latitude` 是纬度字段来源。

拟改进 explorer：

- 在 `entity_hints.py` 中加强“指标字段选择 / 标识列边界 / 谓词落点”探索：
  - 扫描不同表中名称相同、近似或自然语言同义的输出字段，例如 `School` vs ``School Name``、`SOCType` vs ``School Type``。
  - 当一个表拥有地理/状态/度量字段，另一个表拥有更字面匹配的展示字段，且两表有高置信 join 时，创建多实体 `hint`。
  - 给相关实体追加短 `hints`，例如：
    - `schools.SOCType`: “这是 SOC 代码的文本说明；若问题字面要求 `School Type` 且已使用 FRPM 记录，可比较 frpm.`School Type`。”
    - `frpm.`School Type``: “FRPM 记录中的学校类型展示字段；当问题字面要求 school type 且查询涉及 FRPM 覆盖范围时可作为输出字段。”
    - `schools.School`: “学校主表官方名称；若问题字面要求 `School Name` 且以 FRPM 记录为主体，注意 frpm.`School Name` 也存在。”
- 该能力应泛化到其他库：
  - `name` / `display_name` / `title` / `official_name`
  - `type` / `category` / `class` / `status`
  - code 字段与展示字段
  - 维表主字段与事实表快照字段

预期效果：

- solver 在 `meta(schools)` 或 `meta(schools.SOCType)` 时能看到存在 `frpm.School Type` 的候选输出来源。
- solver 不需要主动搜索所有表列，也能意识到“字面字段”和“语义近似字段”存在竞争。

边界：

- explorer 不应直接规定“一定使用 frpm”。它只能说明两个字段的语义边界、来源表和 join 条件。
- 如果问题没有 FRPM 相关线索，且 `schools` 已经足够回答，选择 `schools` 仍可能是合理的。

### 2. 格式修复 hint 过强导致实体集合改变

代表 case：`california_schools` Q43/Q51。

问题现象：

- `satscores.cds` 存在 13 位缺前导零值，`schools.CDSCode` 为 14 位。
- 当前 hint 推荐 `ON s.cds = sc.CDSCode OR '0'||s.cds = sc.CDSCode`。
- 这在数据库完整性上更完整，但会额外纳入直接等值 join 漏掉的记录，改变 top-1 结果。

拟改进 explorer：

- 对前导零、大小写、日期格式、编码格式这类“格式修复”提示，改成中性描述：
  - 说明直接 join 的覆盖率。
  - 说明格式归一化 join 的覆盖率。
  - 明确格式归一化会改变结果集合。
  - 不使用“推荐 JOIN”这种强措辞，除非数据库关系本身已经明确采用该归一化。

建议 hint 形态：

- `satscores.cds`: “与 schools.CDSCode 存在前导零格式差异；直接等值 join 与补零 join 覆盖范围不同，补零会额外匹配 13 位 cds 记录。”
- `satscores.cds->schools.CDSCode`: “直接等值 join 是已登记 FK；若做前导零归一化，需要先确认任务是否要求补全格式差异。”

预期效果：

- solver 会知道 padding 是一个会改变语义集合的选择，而不是默认修复动作。

## Explorer 修改方向

### A. 增强 `entity_hints.py` 的多实体候选字段比较

新增探索重点：

- 找出可通过高置信 join 连接的表对。
- 在表对之间比较列名和 brief/detail：
  - exact token overlap，例如 `School Type` vs `SOCType`。
  - display/name/title/type/status/category/date/amount/count/rank 等高频自然语言词。
  - code 与 label/description/name 的成对关系。
- 对候选字段运行少量 `query` 验证值域和覆盖。
- 写入一个多实体 `hint`，并将一句摘要回写到每个候选列 `hints`。

### B. 增强格式差异 hint 的“覆盖率与风险”表达

新增探索重点：

- 前导零、大小写、trim、日期格式、文本数值等格式归一化。
- 对比直接匹配和归一化匹配的行数、额外匹配行数、是否改变 top/rank 候选。
- hint 表述为“选择风险”，不表述为“推荐修复”。

### C. 统一 hint 呈现

保持当前统一视图：

- 实体自身 `hints`。
- 邻接 `hint` 实体。
- 邻接 `disambig` 实体。

solver 正常 `meta` 基础实体时，应能看到所有相关提示，不依赖主动搜索 `hint`/`disambig`。

## 后续记录模板

每分析完一个数据库或一组错题，按下面格式追加：

```text
### N. <问题类型>

代表 case：
- db / qid

问题现象：
- ...

数据库事实：
- ...

拟改进 explorer：
- ...

预期效果：
- ...

边界：
- ...
```
