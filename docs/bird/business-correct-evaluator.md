# BIRD Business Correct 评测器设计

## 目标

BIRD 评测器判断预测查询的执行结果是否满足 golden result 表达的业务答案。它使用四项输入：

- golden SQL
- golden result
- predicted SQL
- predicted result

其中 result 是正确性的主要证据，SQL 只帮助评测器选择结果比较方法。SQL 写法本身不是评分对象。

## 为什么采用 result-first

同一个业务目标可以由不同 SQL 实现。JOIN 顺序、子查询、CTE、日期函数以及聚合写法都可能不同。如果直接比较 SQL 结构，会把业务等价的实现判错。

另一方面，只把每一列转成独立集合也不可靠。它会破坏同一行内实体与指标的对应关系。例如 `(AAPL, 10)` 和 `(MSFT, 20)` 不能与 `(AAPL, 20)` 和 `(MSFT, 10)` 等价。因此评测器比较完整 row tuple，并在无序场景中把结果视为保留重复次数的多重集合。

## SQL 的辅助职责

SQL 目前只承担两项职责：

1. golden SQL 顶层存在 `ORDER BY` 时，按完整行序列比较结果；否则按无序行多重集合比较。
2. 从两份 SQL 的 SELECT 输出中提取别名、直接列名和规范化表达式，为 golden result 列到 predicted result 列建立候选映射。

SQL 输出映射必须唯一才会使用。`SELECT *`、解析失败、输出数量与实际结果宽度不一致或存在多种映射时，评测器放弃 SQL 提示，回退到结果匹配。

评测器不会因为以下差异直接判错：

- FROM、JOIN 或子查询结构不同；
- WHERE、HAVING、GROUP BY 或 DISTINCT 写法不同；
- SQL AST 不相同；
- predicted SQL 没有匹配到 golden SQL 的输出表达式。

即使 SQL 给出了列映射，映射后的 predicted result 仍必须通过完整结果比较。SQL 不能把不同的结果改判为正确。

## 结果比较规则

当前自动判定遵循以下规则：

- 保留完整行内字段关系。
- 无顶层 `ORDER BY` 时忽略行顺序，但保留重复行次数。
- 有顶层 `ORDER BY` 时比较完整行序列。
- 允许一个对所有行都相同的全局列重排。
- predicted result 可以包含额外解释列，但必须存在一个全局投影，能够完整恢复 golden result 的全部列和全部行。
- predicted 少列、少行或多出无法由 golden result 解释的行时不通过。
- 字符串首尾空白、等价日期表示、数值与数值字符串等安全显示差异可以规范化。
- 浮点数按配置的小数位规范化，不使用任意百分比缩放或无依据的数值容差。

额外列的允许不是“只要包含部分答案就算对”。列投影必须对所有行使用同一映射，投影后的完整 tuple 多重集合或有序序列必须与 golden result 一致。

## 判定流程

```text
golden SQL ──────┐
                 ├─ 生成排序策略和可选列映射
predicted SQL ───┘
                            │
golden result ──────────────┼─ 完整结果比较 ─ business_correct
predicted result ───────────┘                    + match_type
```

评测器先尝试唯一的 SQL 引导列映射。映射后的结果不相等或无法建立唯一映射时，继续使用纯结果比较。这保证 SQL 只能提供帮助，不能形成新的硬拦截条件。

## 为什么不自动放宽所有业务等价情况

仅凭四项输入，有些业务关系无法可靠证明：

- golden 使用 `LIMIT 1`，predicted 返回多行，但排序指标没有出现在结果中时，无法确认额外行是否真的并列。
- `0.12` 与 `12` 可能是比例和百分比，也可能是两个不同数值。
- 原始编码与可读标签可能等价，但四项输入中未必包含编码字典。
- 多行明细与聚合后的字符串可能包含相同信息，但展示结构不足以证明完整对应关系。

这些情况如果靠问题关键词或固定倍率直接放宽，会引入不可解释的误判。因此当前评测器采用保守策略：能够由结果和 SQL 输出信息共同证明时自动通过，证据不足时保持不通过。后续若需要覆盖这些情况，应增加明确、可审计的证据，而不是继续堆文本规则。

## 输出解释

`business_correct` 是唯一主指标。`match_type` 只解释判定路径，例如：

- `exact`
- `value_equivalent`
- `column_reorder`
- `projected_columns`
- `sql_guided_column_reorder`
- `sql_guided_projection`
- `row_count_mismatch`
- `row_bag_mismatch`
- `ordered_row_mismatch`

旧的 strict 指标不参与当前 BIRD benchmark，也不会触发 SQL 重写。
