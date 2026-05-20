#!/usr/bin/env python3
"""BIRD benchmark-level README synchronized into the bird global graph."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.workspace import Workspace


BIRD_README_BRIEF = "BIRD 数据集跨库经验库使用说明"

BIRD_README_DETAIL = """
## BIRD SQL 约定与风格

### 结果列

- 只选择问题明确要求的字段，不附带额外列
- 不要把多个字段拼成一个显示列；如姓名、地址等，保持原列输出
- 多个结果值优先按单列多行返回，不做横向展开或字符串聚合

### 过滤条件

- 不要自作主张添加问题未要求的过滤条件
- 即使元数据提示某列区分记录类型、有效性或粒度，也不要默认加过滤
- 当 evidence 给出了条件值或代码映射，直接按 evidence 翻译
- 当 evidence 与题目表面措辞冲突时，以题目真实语义为准，但不要偏离 evidence 明确给出的公式或编码

### Evidence 翻译

- evidence 给出的列名映射，优先使用
- evidence 给出的计算公式应严格翻译为 SQL，不要简化或改写
- evidence 给出的条件值，直接使用，不要猜测其他值
- evidence 中的代码值映射应直接使用，不要再猜测别的含义
- 当 evidence 明确指出应使用某列时，不要私自换成你认为更接近的列
- 如果 evidence 已经明确给出判断规则，就直接按 evidence 写 SQL，不要再为了“确认同一规则”做多轮试探

### DISTINCT 与 COUNT

- 没有“不同的”“唯一的”这类明确要求时，不要默认加 DISTINCT
- 在 1:N JOIN 中，`COUNT(*)` 或 `COUNT(T1.id)` 统计的是 JOIN 后的行数；不要擅自去重
- 只有当 JOIN 会引入重复、而题目要的是唯一属性结果时，才考虑 DISTINCT

### 输出字段与比例口径

- SELECT 只返回题目要求的目标字段；实体标识字段和显示名称不是同一个输出契约。
- 题目或 evidence 指向 `id`、编号、代码时，返回对应标识字段；只有题目要求名称时才返回名称字段。
- 百分比和 ratio 的分子、分母严格来自题目/evidence，不用不同主键、去重主键或过滤后子集替代分母。
- 如果排序列只是用于确定目标实体，SELECT 中只保留题目要求的返回字段。
- 问题要求月份、年份、日期片段时，返回对应片段表达式；不要返回完整日期或完整年月字段。

### JOIN 选择

- 只 JOIN 问题真正需要的表
- 如果当前表已经有目标列，不要为了“更标准”再 JOIN 另一张等价表
- 在写 SQL 之前，先确认目标列是否已存在于当前表
- `list all` 一类问题，若担心 INNER JOIN 丢行，可考虑 LEFT JOIN
- 写 JOIN 前先确认 `fk` / `rel` / `overlap` / `disambig`
- `fk` 可靠性最高；`rel` 只作辅助；`overlap` 不能直接当 JOIN 条件

### 排序、极值与 Top-N

- `top N`、最高 N 个、最低 N 个，一般优先 `ORDER BY ... LIMIT N`
- 但如果题意允许并列极值，或要求返回所有并列最值，不要机械使用 `LIMIT 1`
- 排名问题若允许并列，优先选择能保留并列语义的写法
- 时间或数值以文本存储时，不要直接按字符串排序；先确认是否存在对应数值列，或显式转换
- 对 `the best / the highest / the richest ...` 这类单数最高级，如果 evidence 已经明确映射到 `max(column)` 且题目没有显式数量词，就直接 `ORDER BY column DESC LIMIT 1`
- `majority` / `most of` 这类“多数/大多数”表达，不等于最高级 `most`；默认先理解成分布或占比问题，优先 `GROUP BY`，不要机械加 `ORDER BY COUNT(*) DESC LIMIT 1`

### 文本数值

- 若某列以 TEXT 存储金额、百分比、时长等带格式的数值，且元数据 / README / 知识已明确这一点，先做清洗与类型转换后再比较或排序
- 不要把证据里的字符串字面量误当作字符串排序规则

### 复合查询

- SQLite 里如果每个复合查询分支都要各自 `ORDER BY / LIMIT`，不要写成顶层 `(SELECT ... LIMIT 1) UNION ALL (SELECT ... LIMIT 1)`
- 先放进 `WITH` / 子查询，再在外层组合

### 限制性定语从句

- 当题目写成 `the X which is cited / used / ordered ... most/least` 这类限制性定语从句时，候选集合应先限制为真正参与该关系的实体
- 不要为了求最小值而用 `LEFT JOIN` 把 `0` 次实体引进来，除非题目显式要求包含 `zero` / `none` / `never`

### 有序端点 / 成对关系

- 对成对关系表、桥接表或有序端点表（如 `*_id1 / *_id2`, `src / dst`, `from / to`）要特别克制
- 题面里出现 `pair`、`both`、`another`，不自动等于“必须双侧对称约束”或“必须同时取两端属性”
- 在 README、FK、已有知识没有明确要求双侧对称时，先从一个已锚定的端点出发建最小 JOIN，再判断是否真的需要补第二侧约束或自连接

### 常见错误归因

- 输出列错是 BIRD 中最常见的失败源：`id`、代码、编号、名称、电话、月份片段是不同输出契约。
- `name` 和 `id` 不能互相替代；Magic/card/player/user 等实体通常同时有显示名和标识列。
- `full name` 在 BIRD 中通常不是拼接字符串；如果 gold 风格或 evidence 指向多个姓名列，返回多个原列。
- 题目要求 `code`、`number`、`id`、`phone`、`zip`、`month` 时，SELECT 只返回这个契约对应的列或表达式。
- top-k 问题常见 gold 口径是 `ORDER BY ... LIMIT N`；只有题意要求所有并列最值时才保留并列。
- `MAX(column)` 得到的是极值本身；`ORDER BY column DESC LIMIT 1` 得到的是拥有极值的行。
- 聚合粒度先由问题目标实体决定，再决定 `GROUP BY`；按 `Segment`、`CustomerID`、`Date`、月份片段聚合会产生不同答案。
- 百分比问题先确定总体分母实体。例如条件在翻译表、交易表、桥表上时，分母仍可能是主实体表。
- `COUNT(DISTINCT ...)` 只在题目要求唯一实体或 JOIN 会重复同一目标实体时使用。
- 题目里的复数名词不必然代表 `DISTINCT`；如果 evidence 用 `COUNT(id)`、`SUM(CASE WHEN ... THEN 1)` 或行级公式，就按行级记录计数。
- `COUNT(CASE WHEN condition THEN 1 ELSE NULL END)` 是 BIRD 常见条件计数写法；百分比公式写了 `Sum(id where condition) / Count(id)` 时，优先翻译成条件聚合。
- 日期、年月和时间戳常需要 `SUBSTR`、`STRFTIME` 或类型转换；返回粒度应匹配问题。
- 执行成功不代表语义正确；空结果、过宽 LIKE、额外过滤、额外 JOIN 都可能产生可执行但错误的 SQL。

### 当前 benchmark 错误经验

- evidence 的公式优先级高于常识改写：写了 `DIVIDE(SUM(x), COUNT(y))`、`LIKE '%value%'`、`STRFTIME('%Y', date)` 时，直接按这个口径落 SQL。
- evidence 明确给出 `LIKE` 时，保持 `LIKE` 口径；只有 evidence 没指定模糊匹配时，才用真实取值检查是否应改成精确匹配。
- 不要为了“更真实”把 evidence 的行级计数改成实体去重计数。`How many artists/cards/products...` 在 BIRD gold 中可能仍按符合条件的记录行计数。
- 不要主动 `ROUND` 比例或均值；题目没有要求保留小数位时，直接返回原始计算结果。
- 日期过滤优先贴合 evidence 的表达式。若 evidence 写的是年份，优先 `STRFTIME('%Y', date)` 或对应年月片段，不用完整日期区间替换。
- TEXT 形式的等级值如果 evidence 直接写数值比较，例如 ``U-PRO > 0 AND U-PRO < 30``，按 evidence 表达式写；不要因为列说明是枚举文本就擅自换成另一套编码解释。
- 字符串时间如 Formula 1 的 `q1/q2/q3` 不是字典序时间；排序最快/最慢时先转成秒数再排序。
- 加过滤条件要有题面或 evidence 支撑。`rtype='S'`、`IS NOT NULL`、状态过滤、有效性过滤、去掉空值等都会改变 BIRD 结果。
- JOIN 链以 gold 目标实体为中心保持最短；如果主表已有可用外键或地区字段，不要绕到客户、持有人、桥表再回来。
- 全库示例只能借鉴 SQL 形状，不能借用外库字段、过滤习惯或结果口径。
- 查询过程中的验证 SQL 不能污染最终 SQL；最终答案只保留题目要求的 SELECT、WHERE、JOIN、GROUP、ORDER。

### BIRD 风格适配

- BIRD 的 gold SQL 经常体现 benchmark 口径，而不一定是唯一合理 SQL；优先贴合题目和 evidence 的字面输出契约。
- schema 元数据、disambig 和 README 是证据，不是自动过滤条件；先判断题目是否真的要求该粒度或条件。
- 读到消歧义实体后，明确区分“列含义说明”和“必须添加 WHERE 条件”。
- 当候选 SQL 在结构上都可执行时，优先选择字段更少、JOIN 更少、过滤更贴近题面、输出契约更直接的一条。
""".strip()


def sync_bird_readme(ws: Workspace) -> None:
    """Synchronize the BIRD README node into the bird global graph."""
    ws.cypher(
        "MERGE (n:knowledge {name: 'README'}) "
        "ON CREATE SET n.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8) "
        "SET n.brief = $brief, n.detail = $detail, n.labels = ['knowledge']",
        params={"brief": BIRD_README_BRIEF, "detail": BIRD_README_DETAIL},
        project="bird",
    )


__all__ = ["BIRD_README_BRIEF", "BIRD_README_DETAIL", "sync_bird_readme"]


def main() -> None:
    ws = Workspace(active_projects=["bird"])
    sync_bird_readme(ws)
    print("Synced BIRD README into bird global graph", flush=True)


if __name__ == "__main__":
    main()
