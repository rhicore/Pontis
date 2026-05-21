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

### 输入证据

- evidence 是题目的一部分，用于给出公式、代码映射、时间范围、字段映射或术语解释
- evidence 给出列名、条件值、代码值或计算公式时，按这些信息翻译 SQL
- evidence 没有覆盖的部分，回到数据库 schema、列说明、样例值和关系图谱判断
- evidence 与 schema 说明同时存在时，先确定它们分别是在说明“字段含义”还是“本题过滤条件”
- evidence 指定的表、列、值、方向和计算顺序优先于语义猜测；不要把明确公式改写成另一种看似等价的口径
- 比较词绑定到具体度量：`amount > 40`、`score >= 90`、`cost < average` 是值过滤；只有题目或 evidence 明说“数量/次数/个数”时才转成 `COUNT(...)`

### 输出契约

- SELECT 只返回题目要求的字段或表达式
- 名称、编号、代码、电话、网址、日期片段、完整日期是不同输出契约
- 题目要求多个字段时，分别返回原字段；只有题目明确要求单个拼接文本时才拼接
- 排序列、过滤列和 JOIN 键只用于定位结果时，不自动加入 SELECT
- 问题要求年份、月份、日期片段时，返回相同粒度的表达式
- 题目要求实体标识、名称、代码或描述时，按数据库中对应字段返回；不要自动替换成另一个更自然的展示字段
- `full name` 若 evidence/schema 指向多个原字段，默认分别输出这些原字段；只有题目要求一个完整字符串时才用拼接表达式
- 关系端点查询保持同一行语义：问“哪些 A 与 B 相连/对应”时输出同一条关系中的端点列，不把两个端点 `UNION` 成单列实体集合

### 最小必要 SQL

- SQL 只表达题目、evidence 和 schema 支持的必要逻辑
- 只有题目或 evidence 要求时才添加格式化、四舍五入、绝对值、字符串拼接和额外排序
- 只有题目、evidence 或 schema 明确要求存在性约束时才添加非空过滤、状态过滤或有效性过滤
- evidence 给出 `A - B`、比例、平均值、条件计数等公式时，保留公式的方向、分子分母和聚合层级
- `ORDER BY` 用于 top/bottom/rank/latest/earliest 或题目明确要求排序的场景；普通列表查询保持自然结果即可
- 图谱或元数据说明存在编码格式问题时，先使用图谱给出的原始 JOIN 键；只有验证结果表明必须清洗、补零或截取时才在最终 SQL 中转换键值
- 行类型、状态、非空、有效记录等列说明用于消歧；题目或 evidence 没有要求时，不把它们加成默认过滤条件

### 过滤条件

- WHERE 条件来自题目、evidence 或明确的 schema 约束
- 元数据中的列含义、枚举说明和样例值用于消歧，不等同于本题必须过滤
- 低基数枚举列优先使用真实存在的精确值
- LIKE 用于题目或 evidence 表达模糊包含、前缀、后缀或模式匹配的场景
- 日期过滤保持题目要求的粒度：年份、年月、日期范围分别对应不同写法

### DISTINCT 与 COUNT

- COUNT 的粒度由题目目标实体决定：行、唯一实体、分组、条件计数是不同口径
- DISTINCT 用于题目要求唯一结果，或 JOIN 会重复同一目标实体而题目目标是唯一实体
- `COUNT(*)`、`COUNT(id)`、`COUNT(DISTINCT id)` 和条件聚合表达不同含义
- 百分比、ratio、average 的分子和分母按题目与 evidence 明确指定的实体集合确定
- 复数名词不自动推出 DISTINCT，先判断数据库中一行代表什么业务实体
- 百分比公式中的分母沿用 evidence 指定的表、行集和实体粒度；不要把行数换成更自然的去重实体数或更大的基础表总数
- “平均每组数量”先按组统计数量再 `AVG`；“满足条件的比例”才使用条件布尔值或条件计数除以总数

### JOIN 选择

- JOIN 链从题目目标实体和已定位字段出发，保持必要且最短
- 当前表已经包含目标字段时，优先直接使用当前表
- 写 JOIN 前确认图谱里的 `fk` / `rel` / `overlap` / `disambig`
- `fk` 表示强结构关系，`rel` 表示可参考的语义关系，`overlap` 表示值重叠线索
- INNER JOIN 适合要求存在匹配关系的查询；LEFT JOIN 适合保留左侧实体并检查缺失关系的查询
- 桥表、事件表、交易表通常改变统计粒度，JOIN 后先重新确认 COUNT 和 GROUP BY 口径
- 不为“看起来相关”的表额外补 JOIN；只有目标字段、过滤条件、输出列或 evidence 需要该表时才加入
- 同一业务对象有多个路径时，先用题目措辞确定路径：拥有者、作者、参与者、交易发生者、记录创建者、关系端点不是同一个角色

### 排序、极值与 Top-N

- top N、最高、最低、最大、最小通常对应 `ORDER BY ... LIMIT ...`
- 题目要求所有并列极值时，用子查询或窗口逻辑保留并列
- `MAX(column)` 返回极值本身，`ORDER BY column DESC LIMIT 1` 返回拥有极值的行
- 文本存储的数值、金额、百分比、时间在排序或比较前需要确认是否应转换
- “多数/占比/比例”类问题先按分组和分母定义处理，再决定是否排序
- 当题目要返回拥有极值的实体或其属性时，用 `ORDER BY` 定位目标行；用 `WHERE value = (SELECT MAX(...))` 只适合题目要求所有并列极值
- “第 N 高/低”使用 `ORDER BY ... LIMIT 1 OFFSET N-1`；不要把 N 改成相邻序号或额外返回前 N 行

### 文本、日期与格式值

- TEXT 列可能存储数值、金额、百分比、时长、日期或代码
- 比较和排序前先通过列说明、样例值和 SQLite 执行结果确认真实格式
- 日期、年份、月份可用 `STRFTIME`、`SUBSTR`、范围条件或显式转换表达
- 输出格式由题目决定；题目未要求格式化时，保留 SQL 计算的自然结果
- 存储为文本的时间/时长值若题目给出显示前缀，优先用等值或前缀匹配验证真实格式；只有题目要求最近/差值/排序时才转成数值计算

### 复合查询

- SQLite 的复合查询中，各分支若需要自己的排序或限制，先放入 CTE 或子查询
- `UNION` 会去重，`UNION ALL` 保留全部行
- `INTERSECT`、`EXCEPT` 适合集合语义明确的题目

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
