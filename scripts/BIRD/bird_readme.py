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
## BIRD SQL 写作决策表

BIRD 题目要求一条可执行 SQLite 查询。最终 SQL 保持最小、直接、可评测的形态：只表达 question、evidence 和当前数据库共同支持的含义，不补充报表化展示、业务清洗或额外解释。

### 1. 总优先级

按以下顺序决策，前一层明确时后一层不得改写：

1. **Evidence**：显式公式、字段映射、代码值、时间范围、比较方向、分母、输出标度。
2. **Question**：输出目标、过滤条件、聚合口径、排序、top-N、是否要求唯一值。
3. **Database context**：schema、列说明、样例值、外键、hints，用于在多个候选表/列/值之间消歧。
4. **BIRD 默认风格**：只在前三层没有明确答案时使用本决策表。

SQL 中的每个 `SELECT`、`WHERE`、`JOIN`、`GROUP BY`、`ORDER BY`、`LIMIT`、`DISTINCT` 都应能由以上某一层解释。

Database context 的职责是识别正确的表、列、值和连接路径。它不能把 question/evidence 没要求的输出列、去重、排序、状态过滤、类别过滤或报表化处理加入 SQL。若 question/evidence 没有授权某个会改变结果形状的 clause，保持更小的 SQL。

对输出列、隐藏过滤、去重、排序和结果粒度而言，授权来源只能是 evidence、question 或本决策表的默认风格。数据库中的行分布、NULL 情况、重复值、展示列、类型列或业务上看起来更合理的解释，都不能单独覆盖本决策表。

### 2. SELECT 输出

| 情况 | 输出规则 |
|---|---|
| evidence 明确映射输出字段或公式 | 按 evidence 输出，不换成更自然的列。 |
| question 明确要求某属性 | 只输出该属性；排序列、过滤列、JOIN 键不自动输出。 |
| question 要实体但未指定展示字段 | 输出该实体的主键、ID、code 或稳定标识。 |
| question 明确要求 name/title/text/label/description | 输出对应展示列。 |
| `Name/List/Find all [entity]` 中的 Name/List/Find 是句首命令动词 | 不算要求 name/title/text/label/description 字段；按“实体但未指定展示字段”输出主键、ID、code 或稳定标识。 |
| 问题写 `their names`、`card name`、`school name`、`named ...` 或 evidence 指向名称列 | 这才算要求名称字段。 |
| 数据库中存在更适合人读的展示列 | 只用于理解实体；只有 question/evidence 要求展示属性时才替代主键、ID 或 code。 |
| 一个概念在库中拆成多列 | 分别输出原字段；只有要求 concatenate/combined/as one string 时才拼接。 |
| 前半句定位实体，后半句要求 its/that entity 的属性 | 最终只输出后半句要求的属性，除非前半句也明确要求输出实体。 |
| yes/no 或状态判断 | 只有问题确实要求判断值时用 `CASE`；词形优先采用 question/evidence/schema 中出现的状态词。 |

列顺序跟随 question/evidence 中的要求；不要额外输出解释列。句首命令动词不能因为数据库里存在同名展示列而改写成展示列输出；只有 question/evidence 明确要求展示属性时才输出展示列。

当需要输出实体标识且存在多个候选标识时，优先选择该实体表的本地主键或字面名为 `id`、`ID`、`code` 的短标识列。跨表外键用的 UUID、全局 ID、外部平台 ID 或长文本标识只在 question/evidence 指向它，或当前 SQL 必须用它表达关系时输出。

当结构化 metadata（如 `primary_key`、列类型、外键列表）与自然语言 detail/hints 冲突时，优先相信结构化 metadata。自然语言 detail/hints 用来解释语义，不改写结构化主键、列类型或外键事实。

### 3. WHERE 过滤

| 情况 | 过滤规则 |
|---|---|
| question/evidence 明确给出条件 | 写入 `WHERE`。 |
| schema 说明某列含义、默认状态或行类型 | 用于理解，不自动变成过滤条件。 |
| schema 存在行类型、记录级别、状态、有效性或类别判别列 | 只有 question/evidence 明确要求该判别维度或对应取值时才过滤；实体名本身不授权这种过滤。 |
| 自然语言中的实体名、角色名、普通形容词 | 不自动转换成隐藏状态、类别、级别或有效性过滤。 |
| 输出属性可能为 NULL | 只有 has/with/non-null/available 等存在性条件明确要求时过滤 NULL；单纯输出该属性时保留 NULL 行。 |
| 固定枚举、代码、ID、完整名称 | 优先等值匹配。 |
| 包含、前缀、后缀、模糊、模式匹配 | 使用 `LIKE` 或 evidence 指定的模式。 |
| 逗号分隔或多值文本列 | 问“包含某值”时用包含匹配；问“整列等于某值”时才等值匹配。 |
| 日期条件 | 保持题目粒度：年、月、日、范围、latest/earliest 分别处理；没有 recent/latest/current 时不自动取最新。 |

普通实体词用于选择目标表、输出对象和连接路径，不等于要求某个记录级别、行类型、状态或类别取值。即使数据库样例显示不加某个判别过滤会返回看起来不够自然的聚合行、汇总行、历史行、无名称行或其他记录类型，也不要仅凭这种观察添加过滤；先服从 question/evidence 是否显式要求该判别维度。`AND`/`OR` 混合时用括号保留 question/evidence 的逻辑。

### 4. 公式和数值

| 情况 | 计算规则 |
|---|---|
| evidence 给出公式 | 按字面实现，保留分子、分母、聚合层级、比较方向、乘以 100 的位置和运算顺序。 |
| evidence 只给出 `A / B` | 输出该公式的字面结果；不要仅因 rate/ratio/proportion 自动乘以 100。 |
| question/evidence 明确 percent/percentage 或公式含 `* 100` | 输出 0-100 标度。 |
| question/evidence 要比例但未指定百分比 | 保持 evidence 公式或数据库列本身的标度。 |
| average/mean、sum、count 等有标准聚合 | 使用对应 SQL 聚合；不要手写等价式改变 NULL 或重复行口径。 |
| 小数位、格式化、四舍五入 | 只有 question/evidence 明确要求时使用 `ROUND`、`printf` 或字符串格式。 |
| 非 SQLite 函数或伪函数 | 翻译为 SQLite 等价表达；日期差优先用 `julianday(end) - julianday(start)`。 |

为避免整数除法，比例计算可把分子转为 `REAL` 或使用浮点常量。

### 5. 粒度、聚合和 DISTINCT

| 情况 | 粒度规则 |
|---|---|
| 开始写 SQL 前 | 先确定一行代表实体、关系、事件、交易、检测、历史记录、快照还是桥表行。 |
| evidence 指定 `COUNT(*)`、`COUNT(id)`、`COUNT(DISTINCT id)`、`SUM(CASE...)` | 保持指定口径。 |
| question 要 list/show/return 具体字段值 | 保持逐行输出，不自动聚合。 |
| question 要 total/how many/average/per group/most common | 使用对应聚合或分组。 |
| 表中已有 count/number/total/rank/score/rate 等指标列，question 要最高/最低该指标对应的行 | 直接按该指标列排序取行；不要仅因为列名含 number/count 就 `SUM`、`COUNT` 或 `GROUP BY`。 |
| question/evidence 未要求 unique/distinct/different | 不自动加 `DISTINCT`。 |
| type/kind/category/status/name 等字段名或概念词出现在计数问题中 | 这些词本身不自动等价于唯一值集合；结合 question/evidence 判断是在统计字段出现的记录、实体行，还是唯一取值。只有 question/evidence 明确写 unique/distinct/different 或指定 `COUNT(DISTINCT ...)` 时，才把 `DISTINCT` 作为强约束。 |
| 多行拥有相同展示值或同一父实体有多个记录 | 这些重复通常是有效结果行；只有 question/evidence 要求唯一值或唯一实体时才去重。 |
| JOIN 到明细、快照、翻译、合法性、交易、历史或桥表 | 默认保持 JOIN 后行粒度；不要因为父实体重复就自动去重。 |
| `ORDER BY ... LIMIT` 定位 top/bottom 行 | LIMIT 作用于原始候选行；除非题目要求唯一值集合，否则不要先 DISTINCT。 |
| 关系或桥表端点查询 | 保持同一关系行的端点，不把两端合并成单列。 |

不要为了“同一个实体身份”“同名实体”“更像自然语言列表”而添加 `DISTINCT`。在 BIRD 风格中，除非 question/evidence 明确要求唯一集合，否则实体列表保持数据库候选行粒度；若需要代表实体且未指定展示字段，优先输出主键、ID、code 或稳定标识。

### 6. 表、列和 JOIN

| 情况 | JOIN 规则 |
|---|---|
| 目标表已包含输出、过滤和公式字段 | 优先直接使用该表。 |
| 需要另一个字段、条件或关系 | 只加入必要 JOIN。 |
| 多张表有相似字段 | 按 question/evidence 的语义和字段来源选择，不因另一列名字更自然而替换。 |
| 普通存在关系 | 使用 `INNER JOIN`。 |
| 题目要求保留无匹配对象、查缺失或包含零记录对象 | 使用 `LEFT JOIN`。 |
| 多条角色路径可达同一对象 | 先区分 owner/author/participant/winner/host/source/target 等角色，再选路径。 |
| 多个同构列或候选槽位 | 只有 question/evidence 要求全部槽位时才 `UNION`；否则使用被指向的代表列。 |

JOIN 键优先使用 schema、外键或 hints 指示的原始键；格式修复只在验证后确实需要时使用。

### 7. 排序、Top-N 和排名

| 情况 | 排序规则 |
|---|---|
| highest/lowest/top/bottom/maximum/minimum 要实体或行 | `ORDER BY metric DESC/ASC LIMIT N`。 |
| 题目要极值本身 | 使用 `MAX`/`MIN` 或等价表达输出值。 |
| 第 N 高/低 | `ORDER BY ... LIMIT 1 OFFSET N-1`。 |
| 要 rank/ranking 值 | 使用窗口排名函数并输出排名列。 |
| 并列 | 只在 question/evidence 要求 ties/all tied 时保留全部并列；否则保持 `LIMIT N`。 |
| most/least common | 通常 `GROUP BY` 目标值并按 `COUNT` 排序；是否输出 count 取决于 question。 |

没有指定 tie-breaker 时，不额外添加稳定排序键。没有要求排序、排名、top/bottom、latest/earliest 或 order-dependent 结果时，保持无 `ORDER BY`；不要为了可读性或稳定输出而排序。

### 8. 文本、时间和复合输出

| 情况 | 规则 |
|---|---|
| TEXT 存数字、金额、百分比、时长、日期或代码 | 比较和排序前用样例或查询确认格式。 |
| when 问发生时间 | 输出具体日期/时间字段；which year/season 才输出年份或赛季标识。 |
| date 和 time 拆列且问题要完整时间 | 输出两个原字段；只问 clock time 时才只输出 time。 |
| XML/HTML/JSON 等结构文本 | 问原字段时输出原值；问自然语言内容时提取内容。 |
| UNION/INTERSECT/EXCEPT | 只用于明确集合运算或多个同构来源必须合并成同一输出 schema。 |

### 9. 最终自查

1. SELECT 是否只包含 question/evidence 要求的列或表达式。
2. WHERE 是否只包含明确条件，没有隐藏状态、有效性、非空或最新记录过滤。
3. 公式是否完全保留 evidence 的分子、分母、标度和聚合层级。
4. COUNT/GROUP/DISTINCT 是否匹配当前 JOIN 后行粒度。
5. ORDER/LIMIT 是否定位 question 要的值、实体或第 N 行。
6. JOIN 是否只为必要字段或关系服务，没有为了“看起来完整”而扩大或缩小候选集。
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
