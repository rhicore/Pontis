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

R03. 决策优先级为 evidence > question > database context > BIRD 默认规则；前一层已经明确时，后一层不能改写它。同一层内部，完整短语的精确表/列/枚举来源优先于少 JOIN、少过滤、少 DISTINCT 等默认风格规则。

R04. Evidence 明确给出字段、公式、代码值、时间范围、比较方向、分母、输出标度或聚合口径时，按 evidence 字面实现；不要用看似等价但实现细节不同的自定义公式、类型转换或日期算法替代 evidence 公式。

R05. Database context 用来识别正确的表、列、值、连接路径和枚举含义，不能单独授权额外输出列、额外过滤、额外排序、额外去重或额外聚合；如果 question 中的完整短语经 metadata、disambig 或查询验证匹配到更精确的表/列/枚举值，必须使用该精确来源，不能为了减少 JOIN 改用语义相近但更粗的表/列/枚举值。

R06. 结构化 metadata、外键、主键和列类型优先于自然语言 hints；hints 只解释语义，不改写结构事实。

R07. `SELECT` 输出列按 question/evidence 的要求排列；排序列、过滤列、JOIN 键不因为参与 SQL 就自动输出。

R08. Question 明确要求某个属性时，只输出该属性，不额外输出实体名、ID、排序指标或解释列；若后续规则要求用实体标识消解多实体匹配歧义，实体标识只作为必要消歧列加入。

R09. Question 要求实体列表但没有明确指定输出属性时，优先选择题目自然要求的展示字段，例如 name、title、label、description 或领域内常用的人可读名称字段。

R10. 只有 question/evidence 明确要求 id、code、key、number、identifier，或没有可靠展示字段时，才默认输出结构化 metadata 标注的本地主键、短 ID、code 或 number；UUID、全局 ID、外部平台 ID 只有 question/evidence 明确要求，或该表没有可用本地主键时才输出。

R11. 触发：候选 SQL 输出 name、title、label、description 等展示字段，但 question 的目标对象是记录、版本、实例、交易、提交、结果、运行、快照、发行版本或印刷版本等行级对象；禁止：在 question/evidence 未明确要求展示字段时，把行级对象改写成展示名称，或用 UUID、全局 ID、外部平台 ID 替代本地主键；执行：优先输出该行所属表在结构化 metadata 中标注的本地主键，只有 question/evidence 明确要求或没有本地主键时才输出 UUID、全局 ID 或外部平台 ID；若 question 明确要求展示字段则按展示字段输出。

R12. 句首 `Name`、`List`、`Find`、`Show`、`Return` 是命令动词；它们本身不等于要求输出名为 `Name` 的列，但如果目标实体在 BIRD gold 风格中通常以人可读名称回答，应按 R09 选择展示字段。

R13. Question 写出 `their names`、`card name`、`school name`、`named ...`、`title`、`text`、`description` 或 evidence 指向名称列时，必须输出对应展示字段。

R14. 前半句用于定位实体、极值行或候选集合，后半句用 `indicate`、`return`、`show`、`list`、`give` 等指令明确要求 its/his/her/that entity 的属性时，最终只输出后半句要求的属性；不要同时输出前半句中用于定位、排序或描述实体的指标，除非 question 明确要求同时输出二者。

R15. 一个概念在库中拆成多列时，分别输出原字段；只有 question/evidence 要求 concatenate、combined、full address、as one string 等单字符串结果时才拼接。

R16. Yes/no 或状态判断只在 question 确实要求判断值时使用 `CASE`；输出词形优先采用 question、evidence 或 schema 中出现的状态词。

R17. `WHERE` 只写 question/evidence 明确要求的条件，或为实现 evidence 字段映射、枚举含义、连接语义、精确短语来源、目标行粒度所必需的条件；不要因为 metadata 提到 status、rtype、active/current、非空、学校级/学区级等字段就自动加入过滤。只有 question/evidence 明确要求该状态/层级，或不加该条件会导致连接路径、目标字段或枚举来源无法表达题意时，才加入这类条件。

R18. schema 中存在状态、类型、行级别、有效性、类别、默认行、历史行或当前行字段，不代表必须过滤。BIRD 风格中，普通实体词（如 school、player、patient、record）通常不足以自动触发隐藏的 rtype/status/active/current 过滤；若 question/evidence 没有点名该层级或状态，优先保持候选表的自然行集。仅当题目明确区分实体行与汇总行，或查询验证显示不加层级条件会选到明显错误对象时，才加入行级别过滤。

R19. 自然语言中的普通实体词、角色词或形容词，不自动转换成隐藏状态、隐藏类别、有效性、非空、最新记录或活跃记录过滤；但 metadata 已经说明的实体行/汇总行粒度选择、连接所需枚举映射、或完整短语精确来源过滤，不属于本规则禁止的隐藏过滤。

R20. 输出实体行时，非输出属性可能为 NULL 不自动过滤；question 明确要求 has、with、available、non-null、known 等存在性条件时才加非空过滤。若 question 直接要求输出某个属性值、名称、标题或文本，结果应是该输出属性的实际值，可过滤该输出列的 SQL NULL；不要额外过滤非 NULL 的占位符、空串或特殊缺失标记，除非 question/evidence 明确要求。

R21. 使用 `ORDER BY ... LIMIT` 选择最高、最低、最新、最早、最大、最小等行时，先判断 SQLite NULL 排序是否真的会改变被选中的行。`DESC LIMIT 1` 下 NULL 默认排在后面，通常不需要额外 `IS NOT NULL`；`ASC LIMIT 1`、最早/最低等场景若 NULL 可能排在前面，且 question/evidence 没有要求缺失值，才添加 `IS NOT NULL`。不要对聚合排序表达式、子查询排序指标或已经由比较条件排除 NULL 的列机械添加 `IS NOT NULL`。

R22. 固定枚举、代码、ID、完整名称优先等值匹配；包含、前缀、后缀、模糊或模式匹配才使用 `LIKE`。

R23. 逗号分隔或多值文本列在 question 问“包含某值”时用包含匹配；只有问整列等于某值时才等值匹配。

R24. 日期条件保持题目粒度；问年只处理年，问月只处理月，问日期范围按范围处理，没有 recent、latest、current、earliest 等词时不自动取最新或最早。

R25. `AND` 和 `OR` 混合时用括号保留 question/evidence 的逻辑。

R26. Evidence 给出公式时保留分子、分母、聚合层级、比较方向、乘以 100 的位置和运算顺序。

R27. Evidence 只给出 `A / B` 时输出字面比例，不因为 rate、ratio、proportion 等词自动乘以 100。

R28. Question/evidence 明确 percent、percentage、百分比，或公式明确包含 `* 100` 时，输出 0-100 标度。

R29. 小数位、格式化、四舍五入只在 question/evidence 明确要求时使用 `ROUND`、`printf` 或字符串格式。

R30. 比例计算为避免整数除法，可以把分子转为 `REAL` 或使用浮点常量。

R31. 触发：SQL 出现 `COUNT`、`SUM`、`AVG`、`MIN`、`MAX` 或 `GROUP BY` 前，必须先确定来源表的一行代表实体、关系、事件、交易、检测、历史记录、快照、明细行、桥表行、汇总行还是已有指标行；禁止：把已有指标行误当成可再次汇总的明细行；执行：只有 question/evidence 明确要求跨行汇总、分组或统计候选行数时才保留聚合，否则回到目标行粒度输出。

R32. 触发：question 要 list、show、return 具体字段值；禁止：因为目标字段看起来可统计就自动 `COUNT`、`SUM`、`AVG` 或 `GROUP BY`；执行：保持逐行输出 question/evidence 指定的字段值。

R33. 触发：question 是 `how many <entities/rows/records>` 且目标是实体、行或记录数量；禁止：计数与 question/evidence 不一致的中间表行、JOIN 后重复行或已有指标列；执行：优先按最终 FROM/JOIN 后的目标行粒度使用 `COUNT(*)` 或 `COUNT(key)`。不要仅因为 question 使用实体名词就自动改成 `COUNT(DISTINCT key)`；只有 question/evidence 明确要求 unique/distinct/different，或查询已确认必要 JOIN 会把同一目标实体重复成多行且题意确实是唯一实体数时，才使用 `COUNT(DISTINCT key)`。

R34. 触发：question 是 `how many/how much/number of/count of <metric>`，且 `<metric>` 已经是表中的人数、数量、次数、分数、排名、rate、count、number、total 等指标列，并且 question 要的是某个或若干匹配实体/记录上的该指标值；禁止：因为有多个匹配实体、或因为题面写了 how many，就默认改写为 `COUNT(*)`、`COUNT(entity)`、`SUM(metric)` 或 `GROUP BY`；执行：直接 `SELECT metric_column`，只保留定位匹配记录所需的 JOIN/WHERE。

R35. 触发：候选 SQL 对已有指标列使用 `SUM`、`AVG`、`COUNT` 或 `GROUP BY`；禁止：把单纯问某实体或若干实体“有多少/多少个/多少量”某指标解释成跨实体总和；执行：只有 question/evidence 出现 total、sum、overall、combined、across all、in total、per group、average、mean、most common 等明确聚合信号，或明确要求统计候选实体/记录数量时才保留聚合，否则改为直接输出指标列。R34/R35 对已有指标列的判断优先于 R69 的一般三分法。

R36. Evidence 指定 `COUNT(*)`、`COUNT(id)`、`COUNT(DISTINCT id)`、`SUM(CASE...)` 等口径时，按指定口径实现。

R37. 触发：候选 SQL 使用 `DISTINCT` 或 `COUNT(DISTINCT ...)`；禁止：为了“更安全”“更像实体列表”“避免重复”而无条件添加；执行：只有 question/evidence 要求 unique/distinct/different，或目标语义明确是唯一取值集合且查询验证显示无 `DISTINCT` 会产生同一取值的非题意重复时才保留，否则删除 `DISTINCT`。BIRD 中 list/how many 默认不等于 unique；JOIN/行粒度可能重复目标对象本身不是保留 `DISTINCT` 的充分理由。

R38. Question/evidence 明确 unique、distinct、different、各不相同、不同取值，或指定 `COUNT(DISTINCT ...)` 时，使用 `DISTINCT`。

R39. 输出目标是实体或取值集合，且 question/evidence 没有要求自然行、前 N 行、记录实例或重复出现次数，JOIN 到翻译、合法性、规则、别名、桥表、明细或历史表只为过滤/解释而引入重复时，可以用 `DISTINCT` 保持目标集合语义。

R40. 触发：计数 SQL 使用 `COUNT(DISTINCT target)`；禁止：在 schema 或查询已确认目标粒度一行一个实体时继续保留 `DISTINCT`，也禁止因为 reviewer 担心 JOIN 可能重复就预防性加 `DISTINCT`；执行：只有 question/evidence 明确唯一口径，或数据库探索已经证明当前 JOIN 会产生同一目标的非题意重复且题目要唯一目标数时才使用 `COUNT(DISTINCT ...)`，否则改为 `COUNT(*)`、`COUNT(key)` 或 evidence 指定口径。

R41. 多行拥有相同展示值不必然是重复错误；如果 BIRD 题目意图是列出行级结果，保留重复行。

R42. `ORDER BY ... LIMIT` 或无排序 `LIMIT N` 定位前 N 条、top/bottom 行或样例行时，默认作用于原始候选行；只有 question/evidence 要求唯一集合、不同实体或不同取值时才先 `DISTINCT`。

R43. 触发：候选 SQL 加入的表没有提供任何 question/evidence 所需的输出字段、过滤字段、枚举解释、精确短语来源或关系路径；禁止：为“看起来完整”额外 JOIN 其他表；执行：删除真正无作用的 JOIN。若 JOIN 表提供了 metadata、disambig 或查询验证指向的更精确字段或枚举值，不能用本规则要求删除。

R44. 需要另一个字段、条件、枚举含义、精确短语来源或关系路径时，只加入服务于这些目的的 JOIN；JOIN 最小化是默认风格规则，不能覆盖 R05、R66、R67、R71 中的精确来源选择。

R45. 触发：多张表有相似字段、同义字段或可组合字段；禁止：只匹配 question 中的一个词就替换成相关但不等价的字段；执行：按 question/evidence 的完整短语、字段来源、列说明和枚举值选择最精确来源，必要时先查询候选枚举值确认。

R46. 触发：候选 SQL 使用 `LEFT JOIN`；禁止：为了覆盖潜在无记录对象、补 0、补 NULL 或保留展示实体而默认左连接；执行：普通存在关系改为 `INNER JOIN`，只有 question 明确要求保留无匹配对象、零记录对象、缺失值、包含没有记录的对象或反向查缺时才保留 `LEFT JOIN`。

R47. 多条角色路径可达同一对象时，先区分 owner、author、participant、winner、host、source、target、teacher、student、buyer、seller 等角色，再选路径。

R48. 多个同构槽位只有在 question/evidence 要求全部槽位时才 `UNION` 或展开；否则使用被明确指向的代表槽位。

R49. JOIN 键优先使用 schema、外键或 hints 指示的原始键。格式修复（补零、CAST、substr、printf 等）只有在查询验证显示原始键连接失败、明显漏行，或目标候选必须通过该修复才能匹配时才加入；不要因为 metadata 提到“可能需要补零/类型修复”就对所有 JOIN 机械修复。若题目所需字段可由两张表直接通过原始键连接得到，优先使用直接连接路径，不额外加入桥表或格式修复。

R50. highest、lowest、top、bottom、maximum、minimum、largest、smallest、most、least、earliest、latest 等要求实体或行时，使用 `ORDER BY metric DESC/ASC LIMIT N`。

R51. Question 要极值本身时，输出 `MAX`、`MIN` 或等价表达；question 要极值对应的实体或属性时，排序取行。

R52. 第 N 高/低使用 `ORDER BY ... LIMIT 1 OFFSET N-1`；并列只有在 question/evidence 要求 ties、all tied、所有并列时才保留全部。

R53. 触发：候选 SQL 使用 `ORDER BY`；禁止：在 question/evidence 没有排序、排名、top/bottom、latest/earliest 或 order-dependent 要求时添加排序；执行：删除不服务于题意的 `ORDER BY`，并同步删除只为排序存在的表达式或 JOIN。

R54. 没有指定 tie-breaker 时，不额外添加稳定排序键。

R55. TEXT 存数字、金额、百分比、时长、日期或代码时，比较和排序前用 schema、样例值或查询确认格式，必要时转换类型。

R56. when 问发生时间时输出具体日期/时间字段；which year/season 才输出年份或赛季标识。

R57. date 和 time 拆列且 question 要完整时间时输出两个原字段；只问 clock time 时才只输出 time。

R58. XML、HTML、JSON 等结构文本，问原字段时输出原值，问自然语言内容时才提取内容。

R59. `UNION`、`INTERSECT`、`EXCEPT` 只用于明确集合运算，或多个同构来源必须合并成同一输出 schema。

R60. 最终自查 `SELECT`：只包含 question/evidence 要求的列或表达式，列顺序与题意一致。

R61. 最终自查 `WHERE`：没有隐藏状态、隐藏类别、活跃记录、非空、最新记录或业务清洗过滤。

R62. 最终自查公式：分子、分母、标度、聚合层级和比较方向与 evidence/question 一致。

R63. 最终自查聚合：`COUNT`、`SUM`、`AVG`、`GROUP BY`、`DISTINCT` 与目标行粒度一致，没有把已有指标列误当成明细行聚合。

R64. 最终自查排序：`ORDER BY`、`LIMIT`、`OFFSET` 只服务于 question/evidence 指定的 top/bottom/rank/latest/earliest 目标。

R65. 最终自查 JOIN：每个 JOIN 都为必要输出字段、过滤条件、枚举解释、精确短语来源或关系路径服务，没有无依据地扩大或缩小候选集；不要仅因为另一个表存在近似字段就替换已由 metadata、disambig 或查询验证支持的 JOIN。

R66. 触发：question/evidence 出现由限定词、方向词、来源词、状态词、对象词和值词组成的复合业务短语；禁止：只实现其中一个词、丢掉修饰词，或用多个语义相近但不等价的字段组合近似表达；执行：先逐词核对候选 SQL 是否覆盖完整短语的每个语义成分，再寻找能整体匹配完整短语的列名、列说明、disambig 或枚举值，若存在则优先使用该单一精确来源，若不存在才拆分为多个必要条件。

R67. 触发：question 的过滤短语可能对应多个表或列，或候选 SQL 只实现了过滤短语的一部分；禁止：在未检查完整列名、列说明、disambig 和枚举值前选择更粗的替代列，也禁止把复合短语拆成一个更宽泛的布尔标志或近似字段后停止探索；执行：优先使用与完整过滤短语更精确匹配的来源表和字段，必要时 JOIN 到该来源表完成过滤，只有不存在精确来源时才考虑语义相近的替代列。R67 优先于 R43/R44 的少 JOIN 默认。

R68. 如果一个表同时含实体级行和上级汇总行，例如 school 与 district、player 与 team、city 与 country，question 指向实体时应选择实体级行；question 指向上级汇总时才选择汇总行。

R69. 触发：候选 SQL 在 `COUNT(*)`、`SUM(metric)` 和 `SELECT metric` 之间做了选择；禁止：因为 question 写 how many 就把三者互相替代；执行：问行数/实体数用 `COUNT`，问明确跨行总量用 `SUM(metric)`，问匹配实体或记录上的已有指标值用 `SELECT metric`。多个匹配实体本身不是 `SUM(metric)` 的充分理由；必须有 total、sum、overall、combined、across all、in total 等总量信号。

R70. 触发：question 要的是已有事实表或度量表中的指标值；禁止：为了覆盖没有匹配指标记录的实体而改用 `LEFT JOIN`、补 0、补 NULL 或额外过滤非输出展示字段；执行：候选集默认来自拥有该指标的匹配记录，除非 question 明确要求包含无记录对象或零值对象。

R71. 触发：同一概念既可由单一精确字段/枚举值表达，也可由多个近似字段组合表达；禁止：在存在精确字段/枚举值时使用近似字段组合，或为了减少 JOIN 而把精确来源替换成近似组合；执行：优先使用单一精确字段/枚举值，组合近似字段只在没有精确来源时使用。

R72. 触发：question 用单数定指实体（如 the customer、the player、the record、the transaction）定位对象并要求该对象的其他属性，但查询验证显示多个不同实体满足同一定位条件；禁止：输出无法区分这些实体的属性列集合后假装是单一对象；执行：若 question 没有进一步限定唯一实体，可加入该实体的本地主键、短 ID、code 或 number 作为必要消歧列，同时仍按 R08/R14 只保留题目要求的属性和必要消歧列。
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
