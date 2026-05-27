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
## BIRD 数据集 SQL 写作约定

BIRD 的题目通常要求一条可执行 SQLite 查询。最终 SQL 优先匹配 question、evidence 和数据库共同限定的结果表形状；答案保持 benchmark 风格的最小表达，而不是报表化、人类解释化或清洗后的展示结果。

### 决策优先级

1. evidence 中的公式、代码值、字段映射、时间范围、比较方向和输出标度优先。
2. question 决定输出字段、过滤条件、排序、聚合、去重、排名和结果行数。
3. schema、列说明、样例值、关系和 hints 用来消歧；它们只在本题需要时进入 SQL。
4. SQL 保持必要逻辑：只加入 question/evidence/schema 明确支撑的 SELECT、WHERE、JOIN、GROUP、ORDER、LIMIT 和表达式。
5. 当自然语言直觉和本约定的默认行为冲突时，采用本约定的默认行为；不要为了更像人工报告而补列、拼接、去重、清洗或改写粒度。

### 输出形态

- `SELECT` 只返回 question/evidence 要求的字段或表达式；排序列、过滤列、JOIN 键、解释辅助列和识别辅助列只有被要求时输出。
- 输出列顺序跟随 question/evidence 中字段出现顺序；特定结构规则优先，例如 `Rank X by Y` 固定输出 X、Y、rank。名称、ID、代码、电话、网址、日期片段和完整日期是不同契约。
- evidence 把一个概念映射到多个字段时，分别输出这些原字段；例如 full name refers to first_name, last_name 时输出两列。只有明确要求 single string、combined text、concatenate 或 as one string 时才拼接。
- `full name`、`name the driver/person/member`、`who is ...` 不自动触发拼接；只要数据库把姓名拆成 forename/surname、first/last 等多列，默认分别输出原字段。
- 同一概念在表中以多列存储时，保持多列输出；例如多个邮箱、电话、管理员字段默认不 `UNION`/unpivot 成单列多行。
- 编号角色字段要区分“多个同类属性”和“多个候选角色槽位”：email1/email2、phone1/phone2 这类同类联系方式可多列输出；administrator1/2/3、player1/2/3 这类编号角色槽位只输出 question/evidence 指向的代表槽位，除非题目明确要求所有槽位。
- `who is`、`which person/driver/member/player` 等询问人物身份时，若存在姓名字段，优先输出姓名原字段；没有姓名字段时才退回主键或 ID。
- 询问非人物实体身份且未指定展示字段时，优先返回该实体表的主键、ID 或稳定标识列；`Name/List/Find all [entity]` 中的动词不等于要求 `name` 列。只有 question 明确要求 name/title/label/text/description 时返回对应展示列。
- 当 question 要求列出记录型实体且未指定具体字段时，只输出该记录实体的主键或稳定标识，不输出整行所有列。
- 当问题使用 which/who/list/find all 识别实体，并且输出展示字段不足以唯一定位行时，可同时输出主键/ID 和展示字段；不要额外输出无关解释列。
- 当 WHERE 中明确列出多个具体 ID、代码或记录标识，且 SELECT 输出的是这些记录的属性值时，同时输出该标识列，让每个属性值对应到原记录。
- 多子句问题中，前面的 `which/what/who ...` 子句常用于定位实体；若后续明确说 `give/list/show/return its Y`，最终 `SELECT` 只输出 Y，除非前一子句也明确要求输出实体身份。
- 枚举值、状态码、类型码、评级码和二值列默认返回数据库原值；只有 question/evidence 明确要求解释文本或没有现成状态列时才用 `CASE` 生成 literal。
- yes/no 判断没有指定词形时使用 `YES` / `NO`。若问题询问 status/level/category 等状态且 evidence/schema 提供 normal/abnormal、positive/negative 等状态词形，优先使用这些状态词形；若 question/evidence 直接指向数据库已有 0/1、Y/N、+/- 或其他状态列，优先输出数据库原值。
- 题目要求判断状态并且需要用阈值或组合条件计算时，使用 `CASE` 表达式；literal 词形优先采用 schema、样例值或 evidence 中出现的大小写。多条匹配记录逐行输出判断结果，除非 question 要求汇总成一个判断。
- `does/is/whether` 开头不必然输出单个 yes/no；如果问题实际要求列出满足条件的行、关系端点或原始状态字段，保持对应行粒度和原始字段。
- NULL 行只在 question 把属性存在性作为筛选条件时过滤，例如 has phone、with website、non-null、available value。若实体已经满足其他 WHERE 条件，电话、姓名、网址、地址、标签等输出属性为 NULL 时保留该实体行并输出 NULL。

### 公式、数值和格式

- evidence 给出的公式按字面实现，保留分子、分母、聚合层级、比较方向、乘以 100 的位置和计算顺序。
- `percent` / `percentage` 通常输出 0-100 标度；`rate` 若表达发生比例也按 0-100 处理；`ratio` / `proportion` 可能输出 0-1 标度。若 question 要 percentage/rate 而 evidence 公式只给出比例分式，通常在该比例结果上乘以 100，除非 evidence 明确要求 0-1。
- 当 evidence 公式包含乘除混合运算时，按公式结构保持分子、分母和乘以 100 的归属；为避免 SQLite 整数除法意外截断，必要时把分子转为 `REAL` 或使用 `100.0`。
- 当公式概念有标准 SQL 聚合函数直接对应时，优先使用标准聚合函数；average/mean 使用 `AVG(col)`，不要手写 `SUM(col)/COUNT(id)` 改变 NULL 分母口径。
- 保留原始数值精度；只有 question/evidence 要求小数位或格式化时使用 `ROUND`、`printf` 或展示字符串。
- 已有比例列和显式公式同时存在时，优先使用 evidence 指定的公式；不要把 0-1 存储比例误当作 percentage 输出。
- `MAX(column)` 返回极值本身；`ORDER BY column DESC LIMIT 1` 返回拥有极值的行。先判断题目要值还是要拥有该值的实体。
- 当 question 询问极值实体的属性时，通常输出实体身份字段；若 question 同时提到排序度量或 evidence 明确映射该度量，可同时输出排序度量列。
- 当 evidence 使用非 SQLite 函数或伪函数时，翻译成 SQLite 等价表达，例如日期差用 `julianday()`/`STRFTIME`，条件计数用 `COUNT(CASE WHEN condition THEN 1 END)` 或 `SUM(CASE WHEN condition THEN 1 ELSE 0 END)`。

### 粒度、聚合和 DISTINCT

- 先确定一行代表什么业务对象：实体、关系、事件、交易、检测、历史记录或桥表行。
- `COUNT(*)`、`COUNT(id)`、`COUNT(DISTINCT id)`、`SUM(CASE...)` 口径不同；evidence 写了哪种就保持哪种。
- 复数名词、users/customers/patients/cards 等实体名词本身不触发 `DISTINCT`。`COUNT(DISTINCT ...)` 只在 question/evidence 明确出现 unique、distinct、different、number of different，或明确要求唯一实体数时使用。
- JOIN 到明细表、快照表、翻译表、合法性表、禁限表、交易表、历史表时，默认保持 JOIN 后行粒度计数；这些表的一行通常就是答案口径的一条记录。
- 当过滤条件来自一对多明细表时，`how many [parent entities] have ...` 默认统计匹配明细行/出现次数；只有明确要求 unique/distinct/different entities 时才收缩为父实体去重计数。
- 在 AVG/COUNT/SUM 等聚合中，即使 JOIN 重复了某个实体，也不要自动去重；聚合默认作用于当前 FROM/JOIN 产生的行集。
- 当 question 要求 list/return/show 具体字段值时，保持逐行输出；只有 total、how many、average、per group、most common、top by count 等表达要求聚合时才 `SUM`、`AVG` 或 `GROUP BY`。
- `how many` 若询问的是表中已有的度量列值，例如 number of test takers、enrollment、population、capacity、score count at/in each matching entity，直接输出该度量列；只有问总数、总体、所有匹配实体合计或没有现成度量列时才聚合。
- “平均每组数量”先按组计数再 `AVG`；“满足条件的比例”按指定分子和分母计算；分母不要扩大到全表或换成更自然的实体全集。
- 单数实体关联到多条记录时，最终行数跟随 question 的单复数和 LIMIT 语义。若 question 只要 the badge/the address/the status 等单个关联属性，使用 `ORDER BY ... LIMIT 1` 或保持单行；若 question 要 list/all records，则展开多行。
- `DISTINCT` 用于 question/evidence 明确要求唯一输出值或唯一输出元组的场景；不要因为 JOIN 看起来重复就提前加 DISTINCT。
- `ORDER BY ... LIMIT` 作用于原始候选行时，不要在 LIMIT 前用 DISTINCT 改变候选集，除非 question 明确要求在唯一值集合中取 top。
- 关系、连接、bond、membership、bridge 等表的一行通常代表一条关系事实；询问关系本身或关系端点时保留关系行粒度，不把两端 `UNION` 成单列，也不只输出其中一端。

### 过滤条件

- `WHERE` 条件来自 question、evidence 或本题明确需要的 schema 约束。
- 元数据中的行类型、状态、有效记录、活动/关闭、样例值和默认值用于理解字段；只有 question/evidence 明确引用对应列名、代码值或状态词时才写入 WHERE。不要把 school/user/customer 等自然语言实体名自动转换成 rtype、status、class、level 等隐藏过滤条件。
- NULL 过滤只由明确的存在性条件触发：has value、non-null、not empty、available address/phone/url 等。若 question 只是要求输出某属性，且行已被其他条件选中，保留 NULL 输出；available/listed/current 等普通修饰词不自动等价于 `IS NOT NULL`。
- 精确枚举、代码、ID、固定名称优先等值匹配；`LIKE` 用于包含、前缀、后缀、模糊匹配或 evidence 指定的模式。
- 当列以逗号分隔或其他方式存储多个枚举值时，题目问“包含某值”使用包含匹配；只有题目要求整列值等于该枚举时才全字段等值匹配。
- 地点和组织维度要精确落列：city、county、district、state、country、school、agency、region 不是同一字段。
- 日期过滤保持题目粒度：年份、年月、日期范围、latest/current/recent/earliest 分别对应不同写法；没有 latest/current/recent/last/most recent 时不要默认取最新记录。
- 比较词绑定到具体度量：`score > 90` 是值过滤；只有题目说数量、次数、个数或 most common 时才转换成计数聚合。
- 人名、地名等文本前缀匹配时，若库中没有 first_name/last_name 分列，BIRD 常用 `LIKE 'Name%'` 前缀匹配；只有需要完整词边界时使用 `LIKE 'Name %'`。
- `AND` 和 `OR` 混合时用括号表达 question/evidence 的真实逻辑；不要让括号改变 evidence 给出的比较组合。

### 表、列和 JOIN

- 同义字段分布在多个表时，按 question/evidence 的语义和字段来源选择；不要因为另一张表字段更自然就替换来源列。
- 当前表已包含目标输出、过滤和公式字段时，优先直接使用当前表；JOIN 只为取得必要字段、条件、关系或 evidence 指定来源。
- JOIN 键优先使用 schema/fk/hints 指示的原始键。补零、截取、大小写转换、`CAST`、`TRIM` 属于格式修复；只有 question/evidence 或已验证的关系要求覆盖这些行时才使用。
- 普通存在关系查询优先 `INNER JOIN`；只有题目要求保留无匹配对象、查缺失、包含无记录对象时使用 `LEFT JOIN`。
- 桥表既可能表示关系本身，也可能表示事件或历史记录。问关系端点时保留同一关系行的端点列；问事件属性时保持事件行粒度。
- 同一业务对象有多个角色路径时先区分角色：owner、holder、author、creator、participant、winner、host、endpoint、record owner 不是同一 JOIN 路径。
- 多个同构列都能 JOIN 到同一实体时，question 要求全覆盖才用 `UNION` 展开全部列；普通关联查询优先使用题目/evidence 指向的代表列。
- 当多个同构列只是同一角色的候选槽位，且 question 没有要求全部槽位，使用题目/evidence 指向的列或最直接的代表列；不要为了覆盖所有可能槽位而自动 UNION。

### 排序、Top-N 和排名

- top/bottom/highest/lowest/maximum/minimum 通常用 `ORDER BY ... LIMIT ...` 定位行；只有题目要求所有并列极值时才保留 ties。
- “第 N 高/低”使用 `ORDER BY ... LIMIT 1 OFFSET N-1`，不要返回前 N 行。
- `rank` / `ranking` 如果要求排名值，使用窗口排名函数并输出排名列。`Rank X by Y` 祈使句默认输出列顺序为 X、排序度量 Y、排名值；该结构优先于通用的 question 字段出现顺序。`ORDER BY` 只控制行顺序，不能替代排名值输出。
- `most common` / `least common` 通常先按目标值 `GROUP BY` 并按 `COUNT` 排序；是否输出 count 取决于 question 是否要求数量。
- 并列极值默认仍取一行；tie-breaker 只有 question/evidence 指定时加入。没有指定 tie-breaker 时保持最小 `ORDER BY metric LIMIT 1`。
- 极值实体 JOIN 到关联明细表后，若 question 是单数输出，`LIMIT 1` 作用于最终结果行；若 question 要 all/list associated records，先定位实体再展开。
- 复数名词不改变 top/bottom/highest/lowest 的默认单行行为；只有 all/every/tied/all tied 等词要求保留并列。
- `at least N`、`top N`、`list N`、`give N` 等明确数量约束限制输出行数；没有 all/every 等展开词时使用 `LIMIT N`。

### 文本、日期和复合查询

- TEXT 列可能存储数字、金额、百分比、时长、日期或代码。比较和排序前用样例值或只读查询确认真实格式。
- 日期、年份、月份可用 `STRFTIME`、`SUBSTR`、范围条件或原值比较；evidence 指定写法时按 evidence。
- when 通常返回具体日期/时间字段；which year/season 才返回年份或赛季标识。
- 当 `when` 询问以年份或赛季标识定位的事件发生时间时，优先输出具体日期/时间列；只有 `which year`、`what season`、`which season` 返回年份或赛季标识。
- `what time` 如果数据库把 date 和 time 拆成两列，返回 date、time 两列作为完整发生时间；只有 question 明确只要 clock time 时才只返回 time。
- XML/HTML/JSON 包裹值按题目口径处理：问题直接引用列或要求原字段时输出原存储值；问题要求 tag/label/category/name 等自然语言内容时，提取无结构标记的内容。
- 简单比较保持简单表达；复杂清洗、白名单、`GLOB`、多重 `LIKE` 或格式归一化只在原始值无法表达题意时使用。
- `UNION`、`INTERSECT`、`EXCEPT` 只用于明确的集合并、交、差，或多个同构来源必须合并为同一输出 schema 的场景。
- `UNION` 的各分支输出列含义一致；不同含义作为不同列，或只返回 question 的主目标。

### 最终检查

1. 确认 SELECT 是否只有 question 要求的列或表达式，并且列顺序正确。
2. 确认 evidence 中的公式、字段、代码、分母、比较方向和标度没有被改写。
3. 确认 JOIN 后的行粒度与 COUNT/GROUP BY/DISTINCT 口径一致。
4. 确认没有无依据的 ROUND、DISTINCT、非空过滤、状态过滤、额外排序、额外 JOIN、拼接列、解释列或 latest 截取。
5. 用只读 SQL 检查关键值是否存在、JOIN 是否产生预期行数、排序或聚合是否定位到正确对象。
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
