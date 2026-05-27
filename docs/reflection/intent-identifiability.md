# Intent Identifiability in Text-to-SQL

## 问题

Text-to-SQL benchmark 通常默认存在一个事实：

> 给定 question、evidence、schema 和数据库内容，golden SQL 是唯一正确意图的表达。

这个假设在很多真实样本上并不成立。模型写错 SQL 可能有三种不同原因：

1. 模型没有理解数据库。
2. 模型理解了数据库，但 SQL 表达风格没有贴合 golden。
3. 题面信息本身不足以唯一确定 golden SQL。

第三类才是更底层的问题。它不是普通 schema linking 错误，而是 **intent 不可识别**：在不知道 golden SQL 的无知之幕下，即使用最强模型通读所有 schema、值分布、样例和说明文件，也无法从输入信息唯一推出 benchmark 作者选择的 SQL 口径。

## 信息论视角

设输入信息为：

```text
I = question + evidence + schema + database values + schema documents
```

设所有在输入信息下可辩护的 SQL 集合为：

```text
S(I) = {sql | sql is defensibly consistent with I}
```

如果 `S(I)` 中所有 SQL 的执行结果等价，那么这个 query 在评测上是可识别的。

如果存在多个非等价 SQL，且每个 SQL 都能被 question/schema/data 支持，那么：

```text
|execution_equivalence_classes(S(I))| > 1
```

该 query 就是 underdetermined。此时 golden SQL 只是多个合理解释之一，而不是由输入信息唯一决定的答案。

这意味着：模型输出与 golden 不一致，并不必然说明模型的数据库理解更差。它可能只是选择了另一个同样合理的意图解释。

## 无知之幕

判断一个错误是否属于 Database Understanding Error，应当先做一个无知之幕测试：

> 不看 golden SQL，只看 question、evidence、schema、数据库内容和模型探索过程。一个足够强的审查者能否证明某个 SQL 是唯一正确的？

如果不能，就不应该直接把该错误归因于数据库理解失败。

更严格的判定问题是：

```text
Can gold be identified from the available information alone?
```

而不是：

```text
Does predicted SQL match gold?
```

## 三类错误

建议把错误归因从二分类扩展成三分类。

### 1. Database Understanding Error

输入信息足以唯一确定正确数据库对象，但模型选错了表、列、路径、值或实体粒度。

例子：

```text
Question asks for client residence district.
Schema clearly distinguishes client.district_id from account.district_id.
Model uses account.district_id.
```

这种错误可以通过更好的图谱、schema summary、disambig、path semantics 来减少。

### 2. Golden SQL Style Error

数据库对象基本正确，但 SQL 表达策略与 golden 不一致。

常见表现：

- 是否 DISTINCT
- GROUP BY 粒度
- tie / ORDER BY / LIMIT 规则
- NULL 处理
- 子查询结构
- 输出列顺序或表达式形式

这类错误更多依赖 prompt、数据集风格约束、few-shot、微调或反思记忆。

### 3. Non-identifiable Intent

仅凭输入信息无法唯一推出 golden。多个 SQL 都合理，golden 依赖隐含作者口径、数据集风格或历史样例。

常见表现：

- 问 “cards” 时输出 `id` 还是 `name` 都可辩护。
- “lap record” 可指 raw lapTimes row，也可指 per-circuit fastest lap。
- 数据库没有 2021 年数据，golden 自动改用 1998，但题面没有说明这一规则。
- “posted by / created by / edited by” 可映射到 owner、last editor 或 history actor，schema 本身不能唯一决定。

这类错误不应简单算作 Pontis 的数据库理解失败。

## 为什么这对 Pontis 重要

Pontis 的目标不是只拟合某个 benchmark 的 hidden convention，而是在无知之幕下建立可解释、可复用的数据理解。

如果一条 query 本身不可识别，那么 Pontis 即使构建了完美 schema graph，也仍然无法保证命中 golden。继续把这类问题归为 Database Understanding Error，会错误惩罚图谱策略。

因此 Pontis 的评估应区分：

```text
Schema-identifiable errors:
  可以通过更好图谱解决。

Convention-dependent errors:
  只能通过 prompt/few-shot/memory/finetune 学到数据集口径。

Non-identifiable queries:
  输入信息不足，golden 不具备唯一可推出性。
```

## 可识别性审查

可以引入一个 blinded adjudicator。它不看 golden SQL，只看：

- question
- evidence
- schema summaries
- relevant table/column samples
- value distributions
- FK/rel/disambig/knowledge
- candidate SQL
- candidate exploration trace

它输出：

```yaml
intent_identifiable: true | false | uncertain
decisive_evidence_available: true | false
plausible_interpretations:
  - interpretation: ...
    sql_sketch: ...
    supporting_evidence: ...
  - interpretation: ...
    sql_sketch: ...
    supporting_evidence: ...
best_effort_sql_is_defensible: true | false
ambiguity_reason: ...
```

如果存在多个可辩护解释，且没有决定性证据排除其他解释，则标为 Non-identifiable Intent。

## 图谱改进和可识别性的关系

更好的 explorer 可以减少很多 Database Understanding Error，但它不能解决所有 Non-identifiable Intent。

Explorer 能增强的信息包括：

- entity grain：一行代表什么实体。
- path semantics：一条 JOIN 路径表达什么业务关系。
- competing candidates：多个相似字段或路径的区别。
- data quality constraints：孤儿行、FK 覆盖率、异常值、数据范围。
- value semantics：枚举值、日期范围、代码含义。

这些能让 `S(I)` 变小，即排除不合理 SQL。

但如果充分探索后仍有多个合理 SQL，说明信息本身不足。此时继续增强图谱也只能描述歧义，不能消除歧义。

## 评测建议

除执行准确率外，建议记录：

```text
Execution Accuracy
Identifiable Accuracy
Non-identifiable Rate
Database Understanding Error Rate
Golden SQL Style Error Rate
Best-effort Defensibility
```

其中：

- `Execution Accuracy`：传统 exact execution match。
- `Identifiable Accuracy`：只在 intent identifiable 的子集上计算。
- `Non-identifiable Rate`：输入信息不足以唯一推出 golden 的比例。
- `Best-effort Defensibility`：预测 SQL 是否是在无知之幕下可辩护的合理答案。

这能更公平地区分：

```text
模型/图谱真的没理解数据库
vs
benchmark hidden convention 没有被输入信息唯一指定
```

## 核心结论

Text-to-SQL 的很多错误不是单纯的模型错误，而是信息唯一对应性问题。

在无知之幕下，如果一个最强模型读完所有可用数据库信息后仍无法唯一选择 golden SQL，那么该 query 不应被视为普通数据库理解失败。

Pontis 应该把目标从“盲目贴合 golden”拆成两层：

1. 在可识别 query 上尽可能写出唯一正确 SQL。
2. 在不可识别 query 上显式暴露歧义，给出最可辩护 SQL，并记录需要额外约定或用户澄清的地方。

