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
## BIRD SQL 写作规则

R01. 最终答案必须是一条可执行 SQLite `SELECT` 查询，输出一个 `sql` 代码块，不输出解释文字。

R02. SQL 只表达 question、evidence 和当前数据库共同支持的含义，不添加报表化展示、业务清洗、额外解释列或人为稳定排序。

R03. 决策优先级为 evidence > question > database context > BIRD priors；前一层已经明确时后一层不能改写。Evidence 中 `refers to`、`means`、公式、枚举值、日期值、比较方向和输出口径按字面实现，除非只是 SQLite 方言转换。

R04. Question/evidence 中的完整短语要整体匹配表、列、枚举或关系；不要只实现其中一个词，也不要在存在单一精确来源时用多个近似字段组合替代。

R05. Database context 用来识别表、列、值、连接路径、枚举含义和行粒度；它不能单独授权额外输出列、额外过滤、额外排序、额外去重、额外聚合或额外 JOIN。

R06. 结构化 metadata、外键、主键和列类型优先于自然语言 hints；hints 解释语义但不改写结构事实。Row grain、一对多和重复信息只用于理解候选行，不能单独授权 DISTINCT、GROUP BY、隐藏过滤、半连接或实体去重。

R07. `SELECT` 只输出 question/evidence 要求的列或表达式，按题意顺序排列；排序列、过滤列、JOIN 键和消歧列不因为参与 SQL 就自动输出。

R08. 多句问题中每个明确要求输出的属性都要输出；定位极值、候选集合或过滤对象的子句只用于找行，除非 question 明确要求同时输出定位指标。

R09. Question 明确要求某个属性、指标、状态或 value 时，只输出该属性/指标/status/value；不要额外输出实体名、ID、code、标签列、解释列、排序指标或过滤指标。

R10. Question 要求实体列表且没有指定输出属性时，优先输出自然展示字段，如 name/title/label/description；句首 Name/List/Find/Show/Return 是命令动词，不等于必须输出 Name 列。

R11. Question/evidence 明确写 names、title、text、description、named 或 evidence 指向名称/标题/文本列时，必须输出对应展示字段；这类明确展示属性优先于默认 ID。

R12. 只有 question/evidence 明确要求 id、code、key、number、identifier，或没有可靠展示字段时，才输出本地主键、短 ID、code 或 number；默认本地 id/code 优先于 uuid/global/external id。

R13. 目标是一行记录、版本、实例、交易、事件、结果、快照、打印、可售对象或其他 row-level object，且 question 没要求展示字段时，输出该行本地主键/短 ID/code，不要改成 name/title/uuid。

R14. 若 question 要求实体及其 ID、name and ID、title and ID 等组合输出，必须同时输出展示字段和本地 ID；不要用 code/number/uuid 同时替代二者，除非 question 点名这些字段。

R15. 一个输出概念在 schema 中拆成多列时，分别输出原字段；除非 question/evidence 明确要求 one string、concatenate、combined text 或完整地址，不要用 `||`、`CONCAT` 或字符串拼接合并。

R16. Yes/no 或自然语言状态只在 question 明确要求 yes/no、true/false、natural-language status，或 evidence 明确给出要输出的词形时才使用 `CASE`；否则保留数据库原始代码/标签。

R17. `WHERE` 只写 question/evidence 明确要求的条件，或为实现 evidence 字段映射、枚举含义、连接语义、精确短语来源和目标行粒度所必需的条件；不要加入隐藏状态、隐藏类型、活跃/当前/默认、最新、非系统或业务清洗过滤。

R18. Schema 中存在 status/type/level/current/valid/default/history 字段不代表必须过滤；普通实体词、角色词、领域形容词或数据库名称不自动转换为隐藏分类过滤。

R19. 输出列为 NULL、空串或缺失值时默认保留；只有 question/evidence 明确要求 known/available/non-null，或同一已定位对象集合中 NULL 只是缺失占位且非 NULL 是唯一有效答案值时，才过滤输出列 NULL。

R20. Nullable 列参与 top/bottom/oldest/youngest/fastest/slowest 等排序取行时，可以过滤 NULL 以避免把未知值当成极值；普通列表查询和普通属性输出不因 nullable 自动过滤。

R21. 固定枚举、代码、ID、完整名称优先等值匹配；包含、多值文本、前缀、后缀、模糊或模式匹配才使用 `LIKE`。

R22. 日期条件保持题目粒度：问年只处理年，问月只处理月，问日期范围按范围处理；没有 recent/latest/current/earliest 等词时不自动取最新或最早。

R23. DATETIME/TIMESTAMP 与日历日期比较时，用 `date(column)` 对齐同一日期粒度；题面时间精度低于列值精度时，用前缀匹配、同精度范围或数值化表达，不要自造最接近的精确值。

R24. `AND` 和 `OR` 混合时用括号保留 question/evidence 的逻辑。

R25. Evidence 给出公式时保留分子、分母、聚合层级、比较方向、乘以 100 的位置和运算顺序；不要把条件聚合中的条件提升到 WHERE 导致分母被过滤。

R26. Evidence 指定 `COUNT(*)`、`COUNT(id)`、`COUNT(DISTINCT id)`、`SUM(CASE...)`、`AVG`、`MAX`、`MIN` 等口径时，按指定口径实现；普通 COUNT 公式没有 DISTINCT 时禁止改成 DISTINCT。

R27. Percent/percentage 或 evidence 明确 `* 100` 时输出 0-100 标度；rate/ratio/proportion 或 `A / B` 本身不自动乘以 100。不要无要求地 `ROUND`、格式化或固定小数位。

R28. 比例计算要让分子和分母使用同一候选行集和同一过滤范围；为避免整数除法可以使用 `REAL` 或浮点常量。

R29. SQL 出现 `COUNT`、`SUM`、`AVG`、`MIN`、`MAX` 或 `GROUP BY` 前，先确定来源表一行代表实体、关系、事件、交易、历史记录、快照、明细行、桥表行、汇总行还是已有指标行。

R30. `How many/count` 先区分三种口径：已有指标值、候选行/记录数、目标实体数。问已有指标列时直接 SELECT 指标；问候选记录数时 COUNT 行；问实体数且一对多 JOIN 会重复目标实体时才 `COUNT(DISTINCT target_key)`。

R31. Question 要 list/show/return 具体字段值时，保持逐行输出该字段；不要因为目标字段可统计就自动 `COUNT`、`SUM`、`AVG` 或 `GROUP BY`。

R32. 当最终来源本身是事件、交易、支付、评论、投票、历史、检测、授予、合法性、打印、关系或其他明细记录表，且 question/evidence 没有 unique/distinct/different，默认按候选记录行计数，不要预防性 `COUNT(DISTINCT foreign_entity_id)`。

R33. `DISTINCT` 只能由 question/evidence 明确 unique/distinct/different、evidence 指定 DISTINCT、或 R30 的目标实体去重例外授权；不要为了“更安全”“避免重复”“更像实体列表”无条件添加。

R34. 输出目标是实体或取值集合，且重复仅由过滤/解释用的一对多 JOIN 引入时，可以用 `SELECT DISTINCT` 保持目标集合；但 row-level records、前 N 行、实例、交易、历史、打印、关系行默认保留重复行。

R35. `GROUP BY` 粒度必须对应 question/evidence 的目标对象；输出属性不自动成为分组键。若问题先定位客户/实体/记录的极值再输出其属性，按目标实体分组或排序后输出属性，不要直接按属性值分组，除非 question 明确问各类别/segment/category 的总体。

R36. Evidence 给出列级谓词或比较条件时，把该谓词放入 WHERE；不要把同一谓词改写成 `SUM/AVG/MAX/MIN/GROUP BY/HAVING`、存在性子查询或跨行累计条件，除非 evidence 本身给出聚合公式。

R37. 计数或比例题不要用半连接、`IN`、`EXISTS` 或先分组再计数把多条明细/关系/事件行压成一个外层实体，除非 question 明确要求外层实体数；evidence 普通 COUNT 或明细扩行口径优先。

R38. 条件作用对象要和被计数行一致：若 evidence 把条件映射到 counted table 的列，默认直接过滤 counted rows；不要先筛 parent entity set 再统计 parent 内所有 child rows，除非 question 明确要求 entity set。

R39. 关系词 connected/linked/paired/bound/related/edge/relationship 且 schema 有关系表或成对端点列时，输出关系记录的两个端点并保留关系行粒度；不要压成单列 DISTINCT endpoint，除非 question 明确要求唯一实体集合或某一侧端点。

R40. 多个标量结果应横向输出为同一个 SELECT 的多列，按 question 顺序排列；不要用 `UNION`/`UNION ALL` 纵向堆成多行，除非 question 明确要求列表或集合运算。

R41. 需要另一个字段、条件、枚举解释、精确短语来源或关系路径时才 JOIN；删除不服务于输出、过滤、枚举解释或关系路径的无用 JOIN。少 JOIN 不能覆盖精确来源要求。

R42. 普通存在关系默认 `INNER JOIN`；不要为了保留潜在无记录对象、补 0、补 NULL 或保留展示实体而默认 `LEFT JOIN`，除非 question 明确要求保留无匹配对象、零记录或缺失值。

R43. 多条角色路径可达同一对象时，区分 owner/author/participant/winner/source/target/home/away/primary/secondary 等角色；不要混用或 UNION 不同角色，除非 question/evidence 明确要求全部角色。

R44. 多个同构槽位只有在 question/evidence 要求全部槽位或使用明确复数输出时才展开；否则使用被明确指向的代表槽位。复数属性可按列分别输出多个槽位，不必 UNION 成行。

R45. JOIN 键优先使用 schema、外键或 hints 指示的原始键；补零、CAST、substr、printf、`+0` 等格式修复只有在跨格式连接必需且查询验证支持时才使用。

R46. 最高、最低、top、bottom、most、least、earliest、latest 等要求实体或行时，用 `ORDER BY metric DESC/ASC LIMIT N`；question 明确要求 ties/all tied 时才返回全部并列。没有指定 tie-breaker 时，不额外添加二级稳定排序键。

R47. Question 要极值本身时输出 `MAX`、`MIN` 或等价表达；question 要极值对应的实体或属性时排序取行。第 N 高/低用 `ORDER BY ... LIMIT 1 OFFSET N-1`。

R48. 没有排序、排名、top/bottom/latest/earliest 或 limit/offset 要求时，不添加 `ORDER BY`；question 明确 alphabetical/alphabetically/by name/title/code/id 时，排序键就是对应展示属性。

R49. When/what date/what time 问发生时间时输出目标行的具体 date/time/timestamp 字段，不要只输出年份、月份、赛季或排序键；date 和 time 拆列且 question 要完整发生时间时输出两个原字段。

R50. XML、HTML、JSON、标签列表或其他结构文本，question 要原字段时输出原值；只有明确要求自然语言内容、元素名或解析结果时才提取。

R51. `UNION`、`INTERSECT`、`EXCEPT` 只用于明确集合运算，或多个同构来源必须合并成同一输出 schema；不要用 UNION 规避角色、槽位或粒度判断。

R52. Final README reviewer 只拦截明确违反 question/evidence/README 的 SQL 形状，如输出列、COUNT/DISTINCT、GROUP BY/HAVING、隐藏过滤、排序、NULL、JOIN 类型、关系端点和格式；不要仅因另一条表/列/JOIN 路径也可能合理而要求换 schema-linking 路径。
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
