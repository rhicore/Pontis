# 关系代数树与查询执行树

## 1. 概念定位

关系代数树（relational algebra tree）或查询树（query tree）是数据库系统内部表示 SQL 查询的一种结构。它不是自然语言语义树，也不是用户意图树，而是把 SQL 转换成一棵由关系代数操作组成的树，用来支持查询优化和查询执行。

一个典型查询树中：

- 叶子节点是输入关系，例如表扫描、索引扫描、临时表或子查询结果。
- 内部节点是关系代数操作，例如选择、投影、连接、分组、聚合、排序、去重。
- 根节点表示最终输出结果。

因此，查询树回答的问题不是“用户到底想问什么”，而是“数据库应该通过哪些关系操作得到这个结果”。

## 2. 和其他树的区别

SQL AST、关系代数树、查询执行树和 NLIDB 语义树经常容易混在一起，但它们关注的层次不同。

| 结构 | 输入 | 关注点 | 典型用途 |
|---|---|---|---|
| SQL AST | SQL 文本 | SQL 语法结构 | 解析、格式化、静态检查 |
| 关系代数树 | SQL 或 AST | 关系操作的逻辑组合 | 查询重写、逻辑优化 |
| 查询执行树 / 执行计划 | 优化后的逻辑计划 | 具体执行算法和物理访问路径 | 数据库执行 |
| NLIDB 语义树 | 自然语言问题 | 用户意图和数据库语义映射 | 交互澄清、Text-to-SQL 中间表示 |

例如：

```sql
SELECT a.name
FROM author a
JOIN paper p ON p.author_id = a.id
WHERE p.year > 2000;
```

SQL AST 会保留 `SELECT`、`FROM`、`JOIN`、`WHERE` 这些语法节点。

关系代数树更像：

```text
Project[a.name]
└── Select[p.year > 2000]
    └── Join[p.author_id = a.id]
        ├── TableScan[author a]
        └── TableScan[paper p]
```

如果进入物理执行计划，节点还会进一步具体化：

```text
Projection[a.name]
└── NestedLoopJoin[p.author_id = a.id]
    ├── SeqScan[author a]
    └── IndexScan[paper p using paper_author_id_idx]
        └── Filter[p.year > 2000]
```

这时已经不只是逻辑关系代数，而包含了执行算法和索引选择。

## 3. 关系代数树的基本操作

关系代数树通常由以下操作构成。

### 选择

选择（selection）过滤行，对应 SQL 中的 `WHERE`、部分 `JOIN ON` 条件。

```text
Select[year > 2000]
└── TableScan[paper]
```

关系代数符号常写作：

```text
σ_year>2000(paper)
```

### 投影

投影（projection）选择列，对应 SQL 中的 `SELECT` 列表。

```text
Project[name, year]
└── TableScan[paper]
```

关系代数符号常写作：

```text
π_name,year(paper)
```

### 连接

连接（join）组合两个关系，对应 SQL 中的 `JOIN` 或由 `FROM` + `WHERE` 隐式形成的连接。

```text
Join[paper.author_id = author.id]
├── TableScan[paper]
└── TableScan[author]
```

连接是查询优化的核心，因为不同 join order 和 join algorithm 可能造成数量级差异。

### 分组和聚合

分组聚合对应 `GROUP BY`、`COUNT`、`SUM`、`AVG`、`MAX`、`MIN` 等。

```text
Aggregate[group_by=author.id, count=COUNT(paper.id)]
└── Join[paper.author_id = author.id]
    ├── TableScan[paper]
    └── TableScan[author]
```

### 排序和限制

排序与限制对应 `ORDER BY` 和 `LIMIT`。

```text
Limit[1]
└── Sort[count DESC]
    └── Aggregate[group_by=author.id, count=COUNT(paper.id)]
```

## 4. 从 SQL 到关系代数树

数据库通常不会直接执行 SQL 字符串，而是经历以下阶段：

```text
SQL text
-> parser
-> SQL AST
-> logical plan / relational algebra tree
-> optimized logical plan
-> physical execution plan
-> executor
```

以这个 SQL 为例：

```sql
SELECT a.name, COUNT(*) AS paper_count
FROM author a
JOIN paper p ON p.author_id = a.id
WHERE p.year > 2000
GROUP BY a.id, a.name
ORDER BY paper_count DESC
LIMIT 10;
```

初始逻辑树可以写成：

```text
Limit[10]
└── Sort[paper_count DESC]
    └── Project[a.name, paper_count]
        └── Aggregate[group_by=a.id,a.name, paper_count=COUNT(*)]
            └── Select[p.year > 2000]
                └── Join[p.author_id = a.id]
                    ├── TableScan[author a]
                    └── TableScan[paper p]
```

优化器可能把 `Select[p.year > 2000]` 下推到 `paper` 表扫描上：

```text
Limit[10]
└── Sort[paper_count DESC]
    └── Project[a.name, paper_count]
        └── Aggregate[group_by=a.id,a.name, paper_count=COUNT(*)]
            └── Join[p.author_id = a.id]
                ├── TableScan[author a]
                └── Select[p.year > 2000]
                    └── TableScan[paper p]
```

这个优化通常更好，因为它先减少 `paper` 的行数，再进行 join。

## 5. 查询优化为什么依赖树

关系代数树重要，是因为 SQL 是声明式语言。用户描述想要什么结果，但没有指定怎么执行。数据库需要把同一个 SQL 转成多个等价计划，并选择成本较低的那个。

常见优化包括：

- 谓词下推：尽早执行过滤，减少中间结果。
- 投影下推：尽早去掉不用的列，减少数据宽度。
- join reorder：改变多表连接顺序。
- join algorithm selection：选择 nested loop join、hash join、merge join 等。
- 子查询改写：把某些子查询改写成 join、semi join 或 anti join。
- 聚合下推：在 join 前提前聚合，降低 join 规模。
- 常量折叠和表达式简化。

例如：

```text
Select[p.year > 2000]
└── Join
    ├── author
    └── paper
```

可以改写成：

```text
Join
├── author
└── Select[p.year > 2000]
    └── paper
```

这两个计划逻辑等价，但后者通常更高效。

## 6. 查询树和查询图

数据库教材中还会区分 query tree 和 query graph。

一般来说：

- Query tree 更适合表示一个具体的关系代数表达式和执行顺序。
- Query graph 更适合表示关系、条件、连接约束之间的无序关系。

例如三表连接：

```sql
SELECT *
FROM A
JOIN B ON A.id = B.a_id
JOIN C ON B.id = C.b_id
WHERE C.status = 'active';
```

Query graph 可以表示成：

```text
A -- A.id = B.a_id -- B -- B.id = C.b_id -- C
                                  |
                         C.status = 'active'
```

而 query tree 必须选择某个连接顺序：

```text
Join[B.id = C.b_id]
├── Join[A.id = B.a_id]
│   ├── A
│   └── B
└── Select[C.status = 'active']
    └── C
```

或者：

```text
Join[A.id = B.a_id]
├── A
└── Join[B.id = C.b_id]
    ├── B
    └── Select[C.status = 'active']
        └── C
```

两棵树可能逻辑等价，但执行成本不同。

## 7. 对 Text-to-SQL 的价值

关系代数树对 Text-to-SQL 至少有四个价值。

### 结构化比较 SQL

直接比较 SQL 字符串很脆弱。下面两个 SQL 字符串不同，但逻辑可能等价：

```sql
SELECT a.name
FROM author a
JOIN paper p ON p.author_id = a.id
WHERE p.year > 2000;
```

```sql
SELECT a.name
FROM paper p
JOIN author a ON a.id = p.author_id
WHERE 2000 < p.year;
```

如果先转成规范化关系代数树，就更容易比较二者是否在投影、过滤、连接和聚合上等价。

### 定位错误发生在哪个操作

Text-to-SQL 错误往往不是整条 SQL 全错，而是某个操作错：

- 投影错：输出列不对。
- 选择错：过滤条件不对。
- 连接错：join path 或 join predicate 不对。
- 聚合错：`COUNT`、`AVG`、`DISTINCT`、分组粒度不对。
- 排序限制错：`ASC/DESC` 或 `LIMIT` 不对。

关系代数树可以把错误定位到节点，而不是只说 SQL 错。

### 构造更细粒度的评测

如果一个预测 SQL 和 gold SQL 执行结果不同，可以比较两棵树的差异：

```text
gold:
Aggregate[group_by=patient.id, count=COUNT(DISTINCT test.id)]

pred:
Aggregate[group_by=patient.id, count=COUNT(test.id)]
```

这说明错误集中在聚合节点的 `DISTINCT` 口径，而不是 schema linking 全错。

### 支持候选 SQL 的等价归并

多候选生成时，很多 SQL 只是写法不同。可以把候选 SQL 转成逻辑树后归并：

- 相同投影、过滤、连接、聚合的候选归为一类。
- 只保留每类中执行更快或更简洁的 SQL。
- 对真正结构不同的候选再做选择或验证。

这比简单字符串去重更可靠。

## 8. 对业务正确性评测的启发

关系代数树本身不能解决自然语言歧义，但能帮助分析“SQL 层到底差在哪里”。

例如 BIRD/Beaver 中常见问题：

```text
业务上都在问患者数量，但一个 SQL 统计 Patient 表实体数，
另一个 SQL 统计 Lab_Test 明细行数。
```

树上可以表现为：

```text
COUNT(DISTINCT Patient.id)
```

对比：

```text
COUNT(Lab_Test.id)
```

这不是表面 SQL 风格差异，而是聚合对象和粒度差异。

再比如 `top 1` 是否保留并列：

```text
Limit[1]
└── Sort[score DESC]
```

对比：

```text
Select[score = Max(score)]
└── ...
```

这两种树表达了不同业务口径：严格取一行，还是返回所有并列最大值。

因此，在新的评测设计中，关系代数树可以作为诊断工具：

- 先比较最终执行结果。
- 如果结果不一致，再比较逻辑树差异。
- 将差异归因到 projection、selection、join、aggregation、order/limit 等节点。
- 再判断这些差异是业务错误、标注风格差异，还是自然语言歧义导致的合理分叉。

## 9. 和 Pontis 的关系

Pontis 当前更关注 schema 语义、业务 hint、数据库知识图谱和 SQL 生成。关系代数树可以作为另一个层次的结构化表示：

```text
Pontis graph:
  说明数据库里有什么，表列和值是什么意思。

Query semantic tree:
  说明用户问题被理解成什么业务意图。

Relational algebra tree:
  说明最终 SQL 通过哪些关系操作得到结果。
```

三者层次不同：

```text
database knowledge graph
-> semantic intent / query interpretation
-> relational algebra tree / logical plan
-> physical execution plan
```

如果未来要做更细粒度的 benchmark 或 SQL guard，可以考虑在 SQL 生成后增加一个逻辑树分析层：

```text
predicted SQL
-> parse
-> normalize to relational algebra tree
-> compare with gold tree or expected operation profile
-> produce clause-level / operation-level feedback
```

这类反馈比“结果错了，反思一下”更具体，也更容易定位业务正确性和执行结果正确性之间的错位。

## 10. 注意边界

关系代数树有几个边界需要明确。

第一，它是 SQL 逻辑/执行层表示，不是自然语言理解层表示。它不能告诉我们用户真正想要哪个业务解释，只能告诉我们某条 SQL 实际采用了什么关系操作。

第二，不同数据库的逻辑计划和物理计划格式不同。PostgreSQL、SQLite、DuckDB、Snowflake 都可能有不同的 explain 输出。若要做跨数据库分析，需要先定义自己的规范化逻辑树。

第三，SQL 等价判断本身很难。两个关系代数树看起来不同，可能在某些约束下等价；看起来相似，也可能因为 NULL、重复行、排序、类型转换、聚合边界而不等价。

第四，关系代数树偏集合/关系语义，而真实 SQL 有 bag semantics、NULL semantics、窗口函数、CTE、递归查询、方言函数等复杂细节。用于评测时要保留这些影响结果的节点。

## 11. 简短结论

关系代数树 / 查询执行树是数据库系统里的成熟概念。它把 SQL 表示成关系操作树，用于查询优化、执行计划生成和结构化分析。

对 Pontis 来说，它最有价值的用途不是替代语义理解，而是在 SQL 生成之后提供一个可比较、可诊断的逻辑结构：

```text
SQL 字符串
-> 关系代数树
-> 操作级差异
-> business correctness / strict execution mismatch 分析
```

这可以帮助把错误从“整条 SQL 错”拆成具体的投影、过滤、连接、聚合、排序或粒度问题。
