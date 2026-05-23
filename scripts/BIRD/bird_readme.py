#!/usr/bin/env python3
"""BIRD dataset-level README synchronized into the bird global graph."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.workspace import Workspace


BIRD_README_BRIEF = "BIRD 数据集通用 SQL 约定"

BIRD_README_DETAIL = """
## BIRD 数据集通用 SQL 约定

### 高优先级写作约定

- BIRD 评测按 SQL 执行结果精确比对；最终 SQL 优先匹配题目、evidence 和数据库给出的最小结果形态，不自动写成更“自然”或更“报表化”的查询
- 不把自然语言合理性覆盖 evidence：evidence 给出的公式、字段、条件值、分子分母、比较方向、输出标度和计算顺序优先
- 如果题目和 evidence 已能直接翻译成简单 SQL，最终 SQL 保持简单；不要主动加入清洗、解释列、去重、排序、格式化、状态过滤、非空过滤或最新记录截取
- `COUNT` 默认不要自动 `DISTINCT`；即使题目自然语言说 patients/accounts/molecules/cards，只要 SQL 已 JOIN 到明细表且 evidence/golden 风格公式是 `COUNT(id)`、`COUNT(*)` 或 `SUM(CASE...)`，通常按 JOIN 后行粒度计数
- 只有题目明确要求 `unique`、`distinct`、`different`、不重复实体，或 JOIN 粒度明显重复同一目标且题目目标是唯一实体时，才使用 `DISTINCT`
- `top`、`highest`、`lowest`、`maximum`、`minimum` 默认用 `ORDER BY ... LIMIT 1` 定位一行；只有题目明确要求 all/every/ties/all with max/min 时，才用 `WHERE value = (SELECT MAX/MIN(...))`、窗口函数或保留并列
- 历史快照表、日志表、实验室检查表、多次测量表中，不要默认取最新记录；只有题目/evidence 出现 latest/current/recent/last/most recent 或明确日期条件时，才加 `ORDER BY date DESC LIMIT 1`
- 题目或 evidence 明确说 `percent` / `percentage` 时，默认输出 0-100 标度，常用 `CAST(numerator AS REAL) * 100 / denominator`；`ratio` / `proportion` 才可能输出 0-1，且仍以 evidence 为准
- evidence 给出简单比较表达式时，最终 SQL 优先保留简单表达式；不要无依据地对 TEXT 数值列添加 `CAST`、`GLOB`、白名单、字符串清洗或格式归一化
- `normal` / `abnormal`、`YES` / `NO`、`well-finished` / `NOT well-finished` 这类状态题，如果 evidence 给出状态映射，SELECT 应输出映射后的状态 literal，而不是原始字段值
- 状态 literal 尽量使用 question/evidence/schema 中出现的词形和大小写；没有依据时使用最短标签，不输出解释性长句
- 关系端点查询先判断输出是 relationship pair 还是 entity set；题目说 connected atoms / atoms of bond / bond endpoints 时，默认保留同一关系行的端点列，不自行 `UNION`/`DISTINCT` 成单列集合
- 不把解释性比较结论自动加进 SELECT；如果 evidence 只定义 percentage/deviation/count 公式，优先只返回公式结果，除非 question 明确要求同时输出哪个类别更多

### 输入证据

- evidence 是题目的一部分，用于给出公式、代码映射、时间范围、字段映射或术语解释
- evidence 给出列名、条件值、代码值或计算公式时，按这些信息翻译 SQL
- evidence 没有覆盖的部分，回到数据库 schema、列说明、样例值和关系图谱判断
- evidence 与 schema 说明同时存在时，先确定它们分别是在说明“字段含义”还是“本题过滤条件”
- evidence 指定的表、列、值、方向和计算顺序优先于语义猜测；不要把明确公式改写成另一种看似等价的口径
- 比较词绑定到具体度量：`amount > 40`、`score >= 90`、`cost < average` 是值过滤；只有题目或 evidence 明说“数量/次数/个数”时才转成 `COUNT(...)`

- evidence 的公式按字面执行，即使公式看起来不符合常规统计习惯；优先保持 evidence 中的聚合函数、条件位置、分母和乘以 100 的顺序
- evidence 给出的术语映射通常是本题绑定，不自动推广成全库默认过滤或默认 JOIN 路径
- evidence 同时给出输出含义和过滤含义时，先区分字段角色：目标输出字段、过滤字段、排序字段、公式字段、JOIN 字段
- question 与 evidence 冲突时，evidence 中的列名、代码值、公式和时间范围优先；question 中的自然语言用于确定目标实体和输出契约
- evidence 有时用简写表达题型意图；例如“最常见/most common”仍通常表示按目标值分组计数后取最高频，而不是返回文本字段的字典序 `MAX`

### 输出契约

- SELECT 只返回题目要求的字段或表达式
- 名称、编号、代码、电话、网址、日期片段、完整日期是不同输出契约
- 题目要求多个字段时，分别返回原字段；只有题目明确要求单个拼接文本时才拼接
- 排序列、过滤列和 JOIN 键只用于定位结果时，不自动加入 SELECT
- 问题要求年份、月份、日期片段时，返回相同粒度的表达式
- 题目要求实体标识、名称、代码或描述时，按数据库中对应字段返回；不要自动替换成另一个更自然的展示字段
- `full name` 若 evidence/schema 指向多个原字段，默认分别输出这些原字段；只有题目要求一个完整字符串时才用拼接表达式
- 关系端点查询保持同一行语义：问“哪些 A 与 B 相连/对应”时输出同一条关系中的端点列，不把两个端点 `UNION` 成单列实体集合

- 枚举值、状态值、类型代码、评级代码通常返回原始数据库值；只有题目要求解释含义时才把代码翻译成自然语言文本
- “what is/which is/list” 后面紧跟的名词通常决定 SELECT 字段；过滤、排序、分组用到的辅助字段不自动输出
- 题目问某指标是否 normal/abnormal/within range 时，通常需要返回由阈值条件计算出的状态表达；不要只返回原始测量值
- 状态 literal 的形式来自 question、evidence 或 schema；没有依据时不要随意把状态改写成 `yes/no`、`1/0` 或任意大小写
- 题目同时要求多个字段时，输出为同一行中的多个列；只有集合语义明确时才使用复合查询
- 题目要求 percentage/ratio/count 同时又说 list IDs/names 时，先判断它是否真的要求两个输出集合；BIRD 里多数问题只需要主要度量或主要实体，不把度量和列表强行 `UNION` 成一列
- `Rank ... by ...` 如果题目要求“rank/ranking”本身，通常需要输出排序指标和 `RANK() OVER (...)`；如果只是要 top/bottom 实体，则用 `ORDER BY ... LIMIT ...`
- 输出字段的顺序按 question 中出现的顺序；同义字段候选存在时，优先使用 evidence 指定的来源表和列

### 最小必要 SQL

- SQL 只表达题目、evidence 和 schema 支持的必要逻辑
- 只有题目或 evidence 要求时才添加格式化、四舍五入、绝对值、字符串拼接和额外排序
- 只有题目、evidence 或 schema 明确要求存在性约束时才添加非空过滤、状态过滤或有效性过滤
- evidence 给出 `A - B`、比例、平均值、条件计数等公式时，保留公式的方向、分子分母和聚合层级
- `ORDER BY` 用于 top/bottom/rank/latest/earliest 或题目明确要求排序的场景；普通列表查询保持自然结果即可
- 图谱或元数据说明存在编码格式问题时，先使用图谱给出的原始 JOIN 键；只有验证结果表明必须清洗、补零或截取时才在最终 SQL 中转换键值
- 行类型、状态、非空、有效记录等列说明用于消歧；题目或 evidence 没有要求时，不把它们加成默认过滤条件

- 不为了“更干净”而添加 `DISTINCT`、`GROUP BY`、`ORDER BY`、`LIMIT`、`IS NOT NULL`、`COALESCE`、`ROUND` 或别名格式化
- 不把 SQL 写成报表查询；BIRD 答案通常是最小 SELECT，避免解释性列、辅助列和格式化展示列
- 候选 SQL 能直接表达题意时，不引入 CTE；CTE 适合窗口函数、分阶段聚合、复合查询分支排序等必要场景
- 多表 join 后不要自动去重；只有目标实体因 join 粒度被重复且问题要求唯一实体时才使用 DISTINCT

### 过滤条件

- WHERE 条件来自题目、evidence 或明确的 schema 约束
- 元数据中的列含义、枚举说明和样例值用于消歧，不等同于本题必须过滤
- 低基数枚举列优先使用真实存在的精确值
- LIKE 用于题目或 evidence 表达模糊包含、前缀、后缀或模式匹配的场景
- 日期过滤保持题目要求的粒度：年份、年月、日期范围分别对应不同写法

- 状态、行类型、有效记录、非空、活动/关闭等字段只有被 question/evidence 明确提到时才过滤
- 样例值和 topk 用于确认真实值拼写；不要因为某列有默认值或大量空值就自动加条件
- 自然语言中的地点、机构、角色、类别要先确认对应列；城市、县、地区、学区、国家、状态不是同一维度
- `LIKE '%value%'` 适合题目说包含/mention/substring；精确枚举、代码、ID、固定名称优先等值匹配
- 区间端点按 question/evidence 的包含性处理；“between A and B”通常包含两端
- 问题说“valid”时先看 evidence 是否定义 valid；没有定义时不要自动等同于非空、活动状态或格式合法

### DISTINCT 与 COUNT

- COUNT 的粒度由题目目标实体决定：行、唯一实体、分组、条件计数是不同口径
- DISTINCT 用于题目要求唯一结果，或 JOIN 会重复同一目标实体而题目目标是唯一实体
- `COUNT(*)`、`COUNT(id)`、`COUNT(DISTINCT id)` 和条件聚合表达不同含义
- 百分比、ratio、average 的分子和分母按题目与 evidence 明确指定的实体集合确定
- 复数名词不自动推出 DISTINCT，先判断数据库中一行代表什么业务实体
- 实验、检测、交易、事件等明细表中，`COUNT(ID)` 不一定表示唯一实体数；如果 evidence 写 `COUNT(ID)`、`COUNT(*)` 或 `SUM(CASE...)`，通常保持明细行粒度
- 百分比公式中的分母沿用 evidence 指定的表、行集和实体粒度；不要把行数换成更自然的去重实体数或更大的基础表总数
- “平均每组数量”先按组统计数量再 `AVG`；“满足条件的比例”才使用条件布尔值或条件计数除以总数

- evidence 写 `COUNT(id)`、`COUNT(*)`、`COUNT(DISTINCT id)`、`SUM(CASE...)` 时按原形式和目标列执行
- 百分比常见写法是 `CAST(numerator AS REAL) * 100 / denominator`；只有题目要求小数位时才 `ROUND`
- “proportion/ratio/percentage of X among Y” 的分母是 Y 对应的行集或实体集，不自动扩大到全表；其中 percentage 通常输出 0-100 标度，ratio/proportion 才可能输出 0-1 标度
- “how many X have condition” 通常是目标实体 X 的 count；“how many records/rows” 才按行数
- “average amount per group/entity” 先明确是 `AVG(amount)`、`SUM(amount)/COUNT(entity)` 还是先分组再 AVG；按 evidence 公式优先
- 条件聚合优先保持在同一 SELECT 中；不为了可读性拆成多个 UNION 分支

### JOIN 选择

- JOIN 链从题目目标实体和已定位字段出发，保持必要且最短
- 当前表已经包含目标字段时，优先直接使用当前表
- 写 JOIN 前确认图谱里的 `fk` / `rel` / `overlap` / `disambig`
- `fk` 表示强结构关系，`rel` 表示可参考的语义关系，`overlap` 表示值重叠线索
- INNER JOIN 适合要求存在匹配关系的查询；LEFT JOIN 适合保留左侧实体并检查缺失关系的查询
- 桥表、事件表、交易表通常改变统计粒度，JOIN 后先重新确认 COUNT 和 GROUP BY 口径
- 不为“看起来相关”的表额外补 JOIN；只有目标字段、过滤条件、输出列或 evidence 需要该表时才加入
- 同一业务对象有多个路径时，先用题目措辞确定路径：拥有者、作者、参与者、交易发生者、记录创建者、关系端点不是同一个角色

- 同义字段分布在多个表时，按 question/evidence 选择来源表；不要自动换成语义更自然但来源不同的列
- 表 A 已经含有目标输出列时，不为了解释来源额外 JOIN 到表 B；反之，题目要求表 B 的专有字段时不要用表 A 的同名字段替代
- 桥表既可能表示关系本身，也可能表示事件/历史/交易记录；问关系端点时保留同一行的端点，问事件属性时保留事件行粒度
- LEFT JOIN 只在题目要求保留无匹配实体、查缺失、包含无记录对象时使用；普通存在关系查询优先 INNER JOIN
- JOIN 键使用数据库中的原始列关系；补零、截取、大小写转换、CAST、TRIM 只有在题目/evidence 或验证结果明确需要时才进入最终 SQL
- 如果图谱关系和 BIRD evidence 指向不同路径，最终 SQL 优先满足 evidence 的字段来源和输出契约

### 排序、极值与 Top-N

- top N、最高、最低、最大、最小通常对应 `ORDER BY ... LIMIT ...`
- 题目要求所有并列极值时，用子查询或窗口逻辑保留并列
- `MAX(column)` 返回极值本身，`ORDER BY column DESC LIMIT 1` 返回拥有极值的行
- 文本存储的数值、金额、百分比、时间在排序或比较前需要确认是否应转换
- “多数/占比/比例”类问题先按分组和分母定义处理，再决定是否排序
- 当题目要返回拥有极值的实体或其属性时，用 `ORDER BY` 定位目标行；用 `WHERE value = (SELECT MAX(...))` 只适合题目要求所有并列极值
- “第 N 高/低”使用 `ORDER BY ... LIMIT 1 OFFSET N-1`；不要把 N 改成相邻序号或额外返回前 N 行

- “rank by” 与 “top N by” 不同：rank 通常需要窗口排名列，top N 通常只需要排序后截取
- “most common/least common” 通常先 `GROUP BY` 目标值并按 `COUNT(...)` 排序；输出是否包含 count 取决于题目是否要求数量
- 如果题目要求 top N 个拥有最高/最低指标的实体或属性，默认按明细行排序后 `LIMIT N`；只有题目要求唯一实体或每个实体聚合时才先按实体分组取 `MAX/MIN`
- “highest average/lowest average” 先判断平均值是已有列还是需要聚合计算；已有 average 列不要再 AVG
- “nth highest/lowest” 使用排序和 offset；不要返回前 N 个，也不要用 `MAX/MIN` 嵌套替代
- 排序 tie-breaker 只有题目/evidence 要求时才添加；额外 tie-breaker 可能改变 BIRD 执行结果

### 文本、日期与格式值

- TEXT 列可能存储数值、金额、百分比、时长、日期或代码
- 比较和排序前先通过列说明、样例值和 SQLite 执行结果确认真实格式
- 日期、年份、月份可用 `STRFTIME`、`SUBSTR`、范围条件或显式转换表达
- 输出格式由题目决定；题目未要求格式化时，保留 SQL 计算的自然结果
- 存储为文本的时间/时长值若题目给出显示前缀，优先用等值或前缀匹配验证真实格式；只有题目要求最近/差值/排序时才转成数值计算

- 题目要求输出代码、状态、日期、时间、金额时，默认输出数据库原值；不自动转成人类解释或重新格式化
- 年份过滤常用 `STRFTIME('%Y', date_col) = 'YYYY'`；若 evidence 指定 `LIKE 'YYYY%'` 或范围条件，则按 evidence
- 日期范围比较优先使用原列可比较格式；只有确认文本格式不适合直接比较时才转换
- 文本数值排序/比较要确认字段真实存储；BIRD gold 有时按原文本/原列计算，避免过度 CAST
- 字符串拼接只用于题目明确要求单个字符串；地址、姓名、多个属性通常分别输出原字段
- 对 evidence 给出的简单数值比较、区间或枚举，最终 SQL 优先保持简单表达；不要无依据地加入手工清洗、额外枚举、LIKE 扩展或非空过滤

### 复合查询

- SQLite 的复合查询中，各分支若需要自己的排序或限制，先放入 CTE 或子查询
- `UNION` 会去重，`UNION ALL` 保留全部行
- `INTERSECT`、`EXCEPT` 适合集合语义明确的题目

- 复合查询只用于题目明确表达集合并、交、差，或必须把多个同构来源合并为同一输出 schema 的情况
- 不用 UNION 把不同含义的输出拼成一列；不同含义的输出应作为同一 SELECT 的不同列，或只返回题目主目标
- 复合查询各分支的列数、列含义和类型必须一致；排序一般放在外层

### 解题顺序

1. 定位题目目标实体和输出契约
2. 从 evidence 提取公式、代码、条件值和字段映射
3. 用 `find` / `meta` 查看候选表、列、关系和消歧义实体
4. 确认 JOIN 链、过滤条件和聚合粒度
5. 用只读 SQL 检查值是否存在、JOIN 是否产生预期粒度
6. 输出最小必要 SQL
""".strip()


def build_bird_readme_system_prompt() -> str:
    """Return BIRD dataset-level SQL conventions as a system prompt section."""
    return BIRD_README_DETAIL


def sync_bird_readme(ws: Workspace) -> None:
    """Synchronize the BIRD README node into the bird global graph."""
    ws.cypher(
        "MERGE (n:knowledge {name: 'README'}) "
        "ON CREATE SET n.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8) "
        "SET n.brief = $brief, n.detail = $detail, n.labels = ['knowledge']",
        params={"brief": BIRD_README_BRIEF, "detail": BIRD_README_DETAIL},
        project="bird",
    )


__all__ = [
    "BIRD_README_BRIEF",
    "BIRD_README_DETAIL",
    "build_bird_readme_system_prompt",
    "sync_bird_readme",
]


def main() -> None:
    ws = Workspace(active_projects=["bird"])
    sync_bird_readme(ws)
    print("Synced BIRD README into bird global graph", flush=True)


if __name__ == "__main__":
    main()
