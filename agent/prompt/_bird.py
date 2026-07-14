"""BIRD benchmark SQL style prompt."""


def get_bird_sql_prompt() -> str:
    return r"""## BIRD Benchmark SQL 风格

当前任务按 BIRD 执行结果评测。请根据 question 和 evidence 写 SQLite SQL，并把 Pontis 图谱作为判断表、列、值和关系的依据。

### 结果粒度

- 先判断题面要的是哪种结果：单个总数/比例/平均值/极值通常返回一行；实体、记录或属性列表通常一行一个答案记录。
- 保持答案行粒度。若原始行已经是题目要求的答案记录，按原始行输出。
- 题面要求按类别或实体汇总时才分组；each、per、by、for each 是分组线索，不是自动 `GROUP BY` 规则。
- 题面要求直接答案表时，不把多个结果改写成 metric/value 或 label/value 报表。

### 指标与聚合

- 先判断所需事实是已有字段、记录计数，还是由组件字段组成的公式。
- 当 question/evidence 指向已有指标字段且没有给出替代公式时，按该字段的存储行粒度使用它。
- 统计记录或实体时使用 `COUNT`；只有明确出现 unique、different、distinct 等去重语义时才使用 `COUNT(DISTINCT ...)`；明确要求跨行总计时使用 `SUM`；明确要求平均值时使用 `AVG`。
- `each`、`per`、`for each`、`by` 等词描述候选粒度；只有题面要求分组汇总时才转成 `GROUP BY`。
- percent、rate、ratio 的计算按 question 或 evidence 指定的数值尺度输出。

### 字段选择

- 遇到歧义字段时先读相关 `disambig`，再选列。
- 优先选择 question/evidence 字面最贴近、且位于当前事实表的原始字段。
- question/evidence 明确指向的字段优先于其他表中语义相近、更规范或可派生的字段。
- evidence 明确给出字段名、代码含义或公式时，按 evidence 的字段口径写 SQL。
- 已有指标字段和 evidence 公式冲突时，以 evidence 明确要求为准。
- 只有题目要求跨表属性，或 evidence 明确指向代码映射时，才切到其他表或代码列。
- 存在直接答案字段时使用直接字段；题面或 evidence 明确要求派生时，再从标识符、年级跨度、名称字符串等字段计算。

### 连接和值匹配

- 优先使用 `fk`、`rel`、已审核 `column_domain` 中已有或经 `query` 验证的原始列简单等值连接。
- 当题面值的实际存储写法不确定时，先检查 sample/top-k，再匹配数据库中存储的文本值或代码值。

### 完成条件

- 形成候选 SQL 前，确定输出列、所需表、连接条件、筛选条件、聚合口径和结果粒度。
- 候选 SQL 成功执行且结果符合 question/evidence 后，直接把它作为最终 SQL 输出。
- 后续工具调用只用于解决仍未确定、并且可能改变最终 SQL 的具体问题。

### 最终输出

只输出一个 SQL fenced code block。代码块内是一条只读 SQLite `SELECT` 或 `WITH ... SELECT` 语句。
"""
