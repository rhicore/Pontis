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

R03. 决策优先级为 evidence > question > database context > BIRD 默认规则；前一层已经明确时，后一层不能改写它。同一层内部，完整短语的精确表/列/枚举来源优先于少 JOIN、少过滤、少 DISTINCT 等默认风格规则。若 evidence 使用 `refers to ... = literal` 明确给出字段值、日期值、枚举值或代码值映射，必须按 evidence 字面值实现，即使 question 中同位置文字不同；不要把这个 literal 当成格式样例、探索候选或可由 question 同位置文字替换的占位符。唯一例外：question 明确写出的实体名、枚举值、数值阈值或日期边界是题目目标本身，且 evidence 只是用 `DIVIDE`、`COUNT`、`CASE`、`IIF` 等公式模板说明字段和计算方式而不是用 `refers to ... = literal` 映射该常量；此时保留 evidence 的字段/公式结构，使用 question 的目标常量。

R04. Evidence 明确给出字段、公式、代码值、时间范围、比较方向、分母、输出标度或聚合口径时，按 evidence 字面实现；不要用看似等价但实现细节不同的自定义公式、类型转换或日期算法替代 evidence 公式。若 evidence 用 `refers to ... = literal` 明确给出过滤值或日期值，按该 literal 实现；这里的 literal 指过滤值和比较方向本身，不表示必须采用会误判列存储粒度的原始字符串比较，也不表示 evidence 只是在演示格式转换。只有当 evidence 的冲突常量出现在公式模板中、且 question 明确给出同一目标条件的常量时，才优先使用 question 常量，同时保留 evidence 指出的字段、公式结构、分母和标度。若 evidence 使用当前 SQLite 不支持的函数名或伪函数（如 `year(date_col)`、`month(date_col)`、`datepart(...)`），必须改写为 SQLite 可执行且同粒度等价的表达式（如 `strftime('%Y', date_col)` 或等价日期范围），这属于方言转换，不是违反 evidence。例外：question/evidence 表达的是日历日期边界，而列值是 DATETIME/TIMESTAMP 时，按 R55 用 `date(column)` 与同一个日期字面量比较，避免把同一天内的时间误算为 after/before；这不算改写 evidence literal。若 evidence 中的符号枚举字面量只因说明文本排版带有首尾空格（如 `' = '`、`' - '`），而查询已确认数据库真实枚举值是不带空格的符号（如 `'='`、`'-'`），使用数据库真实符号值；不要为了机械保留空格而写出 0 命中的过滤值。

R05. Database context 用来识别正确的表、列、值、连接路径和枚举含义，不能单独授权额外输出列、额外过滤、额外排序、额外去重或额外聚合；如果 question 中的完整短语经 metadata、disambig 或查询验证匹配到更精确的表/列/枚举值，必须使用该精确来源，不能为了减少 JOIN 改用语义相近但更粗的表/列/枚举值。

R06. 结构化 metadata、外键、主键和列类型优先于自然语言 hints；hints 只解释语义，不改写结构事实。Metadata 或 hints 中关于 row_grain、一对多、可重复、COUNT(*) 与唯一实体数不同的说明，只用于理解候选行与实体的关系；它们不能单独授权额外 DISTINCT、GROUP BY、隐藏过滤、额外 JOIN 或半连接。计数口径仍由 question/evidence 和 R33/R36/R37/R77/R79 决定。

R07. `SELECT` 输出列按 question/evidence 的要求排列；排序列、过滤列、JOIN 键不因为参与 SQL 就自动输出。若 question 由多个子句组成，前半句明确问某个值/指标/属性（如 `What is the highest X?`、`What is the best metric value?`），后半句再要求对应实体详情（如 `list the entity and context`、`list attributes with ID and date`、`indicate the name ... as well as ...`），最终 SELECT 必须同时包含前半句所问的值/指标/属性和后半句要求的实体详情，并按 question 的自然顺序排列；不要只把前半句的指标用于 `ORDER BY`、`WHERE` 或 `LIMIT` 而不输出。

R08. 触发词：give/show/state/return/list/indicate their property/status/metric/value、attribute value、output column、SELECT extra column。Question 明确要求某个属性、指标或 value 时，只输出该属性/指标/value，不额外输出实体名、ID、code、标签列、解释列、排序指标、过滤指标或 JOIN 键；例如只问 attribute value 时输出 value 列，不额外输出 attribute name/label。若 evidence 明确把问题短语映射到某个输出列或表达式，例如 `rule ... refers to format`、`status refers to ...`、`value refers to ...`，SELECT 应直接输出该列/表达式，不要与其他列拼接成复合字符串，也不要额外输出相邻的状态、标签、名称或解释列。若后续规则要求用实体标识消解多实体匹配歧义，实体标识只作为必要消歧列加入。若 question 形式为“for all/the entities that satisfy condition, give/state/show/indicate their <property/status/metric/value>”，最终只输出 `<property/status/metric/value>`。只有 question 明确写出 give/list/show/provide IDs、along with their IDs、output both entity and property 等输出要求时才加入实体标识。

R09. 触发词：list/show/return/find entities、name/title/label/description、human-readable display field。Question 要求实体列表但没有明确指定输出属性时，优先选择题目自然要求的展示字段，例如 name、title、label、description 或领域内常用的人可读名称字段；本规则只适用于实体概念本身，不适用于题目目标是行级记录、版本、实例、交易、提交、事件或快照的场景。

R10. 触发词：id/code/key/number/identifier/local primary key/UUID/global ID/external ID。只有 question/evidence 明确要求 id、code、key、number、identifier，或没有可靠展示字段时，才默认输出结构化 metadata 标注的本地主键、短 ID、code 或 number；稳定标识优先使用本地主键或本地短 ID，不因存在 UUID、全局 ID 或外部平台 ID 就输出它们。若同一目标行同时有本地主键/短 ID/code/number 和 UUID/global/external ID，默认输出本地主键/短 ID/code/number；列名本身为 `id`、`<entity>_id`、`<entity>Id` 且 metadata 描述为主键、自增、内部唯一标识、本地编号或本地代码时，就是本地行标识，优先级高于 `uuid` 或外部平台 ID，即使 JOIN 必须使用 uuid。若同一表同时存在 `id` 和 `code`/`number`/`slug`，question 写 ID 时默认输出 `id`；只有 question/evidence 明确写 code、number、slug、short code、abbreviation 或 metadata 说明该表没有可用 `id` 时，才用 code/number/slug 代替 ID。UUID、全局 ID、外部平台 ID 只有 question/evidence 明确点名，或该表没有可用本地行标识时才输出。若 question 要求 `entities with their IDs`、`list entities and their IDs`、`name and ID`、`title and ID` 等组合输出，最终 SELECT 应同时包含该实体的人可读展示字段和本地 ID；不要只输出 code/number/slug 来同时替代实体展示字段和 ID，除非 question 明确要求 code/number/slug 而不是 ID。

R11. 触发词：row-level object、record/version/instance/transaction/event/result/run/snapshot/release/printing/print card/printed card/card printing/printed item/edition/item/object、SELECT name/title/label/description/uuid。候选 SQL 输出 name、title、label、description、uuid/global_id/external_id 等展示字段或全局标识，但 question 的目标对象是一行代表一个记录、版本、实例、交易、提交、事件、结果、运行、快照、发行版本、印刷版本、印刷卡、印刷对象、可售对象或其他行级对象；禁止：在 question/evidence 未明确要求展示字段或全局标识时，把行级对象改写成展示名称，或用 UUID、全局 ID、外部平台 ID 替代本地主键；执行：优先输出该行所属表在结构化 metadata 中标注的本地主键/本地短 ID/code/number。若 metadata 同时展示 `id` 与 `uuid`，且 `id` 被描述为主键、自增、内部唯一标识、本地标识或本地编号，则 `id` 是要输出的本地行标识；uuid 只作为 JOIN 键或外部全局标识，不应作为最终输出。若 question 只说 list/show/return row-level objects、print cards、printed cards、card printings 或 printed/edition/version records，没有写 names/titles/text/descriptions/foreign names/uuid/global id/external id 等展示属性，则 `SELECT name/title/label/description/uuid/global_id/external_id` 必须改为本地行标识。

R12. 触发词：Name/List/Find/Show/Return as command verb。句首 `Name`、`List`、`Find`、`Show`、`Return` 是命令动词；它们本身不等于要求输出名为 `Name` 的列，但如果目标实体在 BIRD gold 风格中通常以人可读名称回答，应按 R09 选择展示字段。

R13. 触发词：their names、name/title/text/description、named、display property。Question 明确写出 `their names`、`<entity> name`、`named ...`、`title`、`text`、`description` 或 evidence 指向名称/标题/文本列时，必须输出对应展示字段；这类明确展示属性要求优先于默认输出 ID 的规则。

R14. 触发词：indicate/return/show/list/give its/his/her/that entity property、target property after locating entity。前半句用于定位实体、极值行或候选集合，后半句明确要求 its/his/her/that entity 的属性时，最终只输出后半句要求的属性；不要同时输出前半句中用于定位、排序、过滤或描述实体的指标，除非 question 明确要求同时输出二者。

R15. 一个输出概念在库中拆成多列时，分别输出原字段，不要自行拼接；例如 name/person name 若由 first/last、forename/surname、given/family 等多列组成，应输出这些原字段。`his name`、`her name`、`their names`、`person name`、普通 `name`、`full name refers to first+last` 或 evidence 中的 `name refers to col1+col2` 都只说明姓名由多个源字段组成，不等于要求把多个字段拼成单字符串。候选 SQL 若用 `||`、`CONCAT`、字符串连接符把这些原字段拼成一个 name/full name 列，而 question/evidence 没有明确要求 concatenate、as one string、single string、one column、combined text、完整地址等单字符串结果，则违反本规则；执行时删除拼接表达式，改为按原字段分别 SELECT。该规则属于结果形状约束：即使拼接后的字符串看起来更自然、报告中称为“full name”或“完整姓名”，也不能替代多个原始输出列。

R16. Yes/no 或状态判断只在 question 明确要求输出 yes/no、true/false、自然语言状态词，或 evidence 明确给出要输出的状态词时才使用 `CASE`；若 evidence 只是说明数据库代码/标签的含义，例如 `label = '+' means ...`，BIRD gold 风格通常保留原始代码/标签输出，不把它翻译成自造文本。

R17. 触发词：WHERE hidden filter、status/type/category/level/active/current/default/history/valid/non-null filter。`WHERE` 只写 question/evidence 明确要求的条件，或为实现 evidence 字段映射、枚举含义、连接语义、精确短语来源、目标行粒度所必需的条件；不要因为 metadata 提到状态、类型、类别、层级、有效性、活跃/当前、默认/历史、非空等字段就自动加入过滤。只有 question/evidence 明确要求该状态/类型/层级，或不加该条件会导致连接路径、目标字段或枚举来源无法表达题意时，才加入这类条件。

R18. 触发词：schema has status/type/row level/valid/current field but question does not mention it。schema 中存在状态、类型、行级别、有效性、类别、默认行、历史行或当前行字段，不代表必须过滤。BIRD 风格中，普通实体词通常不足以自动触发隐藏状态、隐藏类型、活跃、当前或层级过滤；若 question/evidence 没有点名该层级或状态，优先保持候选表的自然行集。仅当题目明确区分实体行与汇总行，或查询验证显示不加层级条件会选到明显错误对象时，才加入行级别过滤。

R19. 自然语言中的普通实体词、角色词或形容词，不自动转换成隐藏状态、隐藏类别、有效性、非空、最新记录或活跃记录过滤；但 metadata 已经说明的实体行/汇总行粒度选择、连接所需枚举映射、或完整短语精确来源过滤，不属于本规则禁止的隐藏过滤。

R20. 触发词：IS NOT NULL、NULL、empty string、known/available/available website URL/available URL/has/with property。输出实体行时，非输出属性可能为 NULL 不自动过滤；只有 question/evidence 明确要求 has/with non-null/known/available record、available item、存在某属性、属性已知等存在性条件时才加非空过滤。若 question 直接要求输出某个属性值、名称、标题、文本、URL、website URL 或描述，把 available/known 作为输出属性修饰词时，默认输出该属性列的实际值，可包含 SQL NULL；不要额外过滤 NULL、非 NULL 占位符、空串或特殊缺失标记。文本名词本身（comments/text/notes/descriptions/history entries 等）不是非空或非空串要求；`all comments`、`all text`、`all notes`、`all history records` 这类完整行集请求默认保留匹配行上的原始输出值，即使该值为空串或 NULL。窄例外：若最终输出只有该 nullable 属性值本身，且查询验证显示同一已定位实体/对象/版本集合中存在 NULL 占位行与非 NULL 实际属性值混杂，NULL 不是有效答案值而只是缺失属性占位，则最终 SQL 必须对输出属性列加 `IS NOT NULL`，避免把缺失占位作为答案返回；不加该过滤会额外返回 NULL 答案行，属于输出值有效性错误和非 schema README 违规，reviewer 必须拦截。该例外不适用于输出实体行、输出 URL/website URL 时 question 使用 available/known 作为属性修饰词的场景，也不适用于 question 明确要求 all matching rows/comments/text/notes/descriptions/history entries 或包含 missing/unknown/NULL/empty 的场景；这类题目要求完整行集时，不要用输出列非空或非空串过滤来清洗结果。若 SQL 对输出列写 `IS NOT NULL`、`<> ''` 或缺失值过滤，而既不满足明确存在性要求，也不满足上述输出属性值占位例外，则必须删除该非空过滤。

R21. 使用 `ORDER BY ... LIMIT` 选择最高、最低、最新、最早、最大、最小等行时，先判断 SQLite NULL 排序是否真的会改变被选中的行。`DESC LIMIT 1` 下 NULL 默认排在后面，通常不需要额外 `IS NOT NULL`；`ASC LIMIT 1`、最早/最低等场景若 NULL 可能排在前面，且 question/evidence 没有要求缺失值，才添加 `IS NOT NULL`。不要对聚合排序表达式、子查询排序指标或已经由比较条件排除 NULL 的列机械添加 `IS NOT NULL`。

R22. 固定枚举、代码、ID、完整名称优先等值匹配；包含、前缀、后缀、模糊或模式匹配才使用 `LIKE`。

R23. 逗号分隔或多值文本列在 question 问“包含某值”时用包含匹配；只有问整列等于某值时才等值匹配。

R24. 日期条件保持题目粒度；问年只处理年，问月只处理月，问日期范围按范围处理，没有 recent、latest、current、earliest 等词时不自动取最新或最早。

R25. `AND` 和 `OR` 混合时用括号保留 question/evidence 的逻辑。

R26. Evidence 给出公式时保留分子、分母、聚合层级、比较方向、乘以 100 的位置和运算顺序。若 evidence 写成 `DIVIDE(COUNT(col = value), COUNT(col))`、`SUM(CASE WHEN col = value THEN 1 ELSE 0 END) / COUNT(col)` 等条件聚合公式，必须把 `col = value` 保留在聚合表达式内部；不要把该条件提升到 `WHERE` 导致分母也被同一条件过滤。若 evidence 写出 `MAX(COUNT(col))`、`MIN(COUNT(col))`、`COUNT(col)`、`AVG(col)`、`SUM(col)` 或同类聚合表达式，候选 SQL 应保留 evidence 指定的聚合目标和由该目标 implied 的分组粒度；不要因为主键更稳定、`COUNT(*)` 更常见、或另一个列名更像实体标识，就把 `col` 换成主键、`*`、JOIN 键或其他字段。只有 question/evidence 另行明确指定不同聚合目标，或该列在当前 schema 中不存在且已有 metadata/evidence 指向等价替代列时，才可替换。其他 question/evidence 给出的筛选条件仍可放在 `WHERE` 中形成该公式的候选行集。

R27. Evidence 只给出 `A / B` 时输出字面比例，不因为 rate、ratio、proportion 等词自动乘以 100。

R28. Question/evidence 明确 percent、percentage、百分比，或公式明确包含 `* 100` 时，输出 0-100 标度。百分比分母必须与分子使用同一候选行集和同一时间/类别过滤；不要把“某月/某类/某条件下的 percentage of customers/records”除以全库主表总行数，除非 question 明确要求全体注册客户、全体实体或全库总体作为分母。

R29. 触发：候选 SQL 出现 `ROUND`、`printf`、`FORMAT`、`CAST(... AS TEXT)`、字符串格式化或固定小数位。小数位、格式化、四舍五入只在 question/evidence 明确要求 decimal places、round、rounded、四舍五入、保留几位小数、format/格式化时使用。percentage/percent/ratio/rate 本身不是舍入信号；若 evidence 给出百分比公式但没有指定小数位，输出原始数值表达式，不要包 `ROUND(..., n)`。

R30. 比例计算为避免整数除法，可以把分子转为 `REAL` 或使用浮点常量。

R31. 触发：SQL 出现 `COUNT`、`SUM`、`AVG`、`MIN`、`MAX` 或 `GROUP BY` 前，必须先确定来源表的一行代表实体、关系、事件、交易、检测、历史记录、快照、明细行、桥表行、汇总行还是已有指标行；禁止：把已有指标行误当成可再次汇总的明细行；执行：只有 question/evidence 明确要求跨行汇总、分组或统计候选行数时才保留聚合，否则回到目标行粒度输出。

R32. 触发：question 要 list、show、return 具体字段值；禁止：因为目标字段看起来可统计就自动 `COUNT`、`SUM`、`AVG` 或 `GROUP BY`；执行：保持逐行输出 question/evidence 指定的字段值。

R33. 触发词：how many、count、COUNT、COUNT(DISTINCT)、entity count、row count、record count、unique/distinct/different。先区分三种计数口径：已有指标值、候选行/记录数、目标实体数。若 question 问的是表中已有指标列（如 `<metric_count_col>`、`<score_count_col>`、`<tested_count_col>` 等），按 R34/R35 直接输出指标列。若 evidence 显式给出 `COUNT(column)`、`Divide(COUNT(column ...), COUNT(column ...))` 或类似普通 COUNT 公式，且没有写 DISTINCT，则这是最高优先级的公式口径：候选 SQL 中同一公式项必须使用普通 `COUNT(column)`、`COUNT(CASE WHEN ... THEN column END)` 或等价普通行级计数，不得改成 `COUNT(DISTINCT column)`；即使 question 写的是 `percentage of <entities>`、`how many <entities>` 或自然语言看起来像实体集合，也不能覆盖 evidence 的普通 COUNT 公式。若 question 是 `how many <entity plural>`、`number of <entity plural>`，目标是满足条件的实体数量，且 evidence 没有给普通 COUNT 公式，同时 SQL 为了实现过滤条件必须从目标实体表 JOIN 到事件表、明细表、桥表、历史表、翻译表、合法性表等一对多来源，JOIN 后会重复同一个目标实体，则可以使用 `COUNT(DISTINCT target_entity_key)` 计数目标实体；这不是“为了安全去重”，而是保持 question 的目标实体口径。若最终候选行本身就是获得、发生、交易、检测、投票、评论、历史、合法性、打印、授予等事件/关系/记录行，且没有额外从目标实体表 JOIN 到一对多来源来扩展同一个目标实体，则按候选行/记录计数，不要仅因 question 使用 users/customers/items 等复数实体词、metadata/hints 说明同一实体可出现多次、或查询发现 `COUNT(*)` 与 `COUNT(DISTINCT key)` 不同就改成 DISTINCT。若 question/evidence 明确写 row/record/entry/event/history/transaction/matching record，则按候选行/记录口径 `COUNT(*)` 或 `COUNT(column)`，不要 DISTINCT。若 question/evidence 明确出现 unique/distinct/different/不同/唯一，或指定 `COUNT(DISTINCT ...)`，必须使用 DISTINCT 口径。

R34. 触发：question 是 `how many/how much/number of/count of <metric>`，且 `<metric>` 已经是表中的人数、数量、次数、分数、考生数、测试人数、浏览量、消费状态、排名、rate、count、number、total 等指标列，并且 question 要的是某个或若干匹配实体/记录上的该指标值；禁止：因为有多个匹配实体、或因为题面写了 how many/number，就默认改写为 `COUNT(*)`、`COUNT(entity)`、`SUM(metric)` 或 `GROUP BY`；执行：直接 `SELECT metric_column`，只保留定位匹配记录所需的 JOIN/WHERE。例如“state the number of views of users who ...”默认输出匹配用户各自的 `Views`，不是 `SUM(Views)`，除非 question/evidence 明确写 total/sum/overall/combined。

R35. 触发：候选 SQL 对已有指标列使用 `SUM`、`AVG`、`COUNT` 或 `GROUP BY`；禁止：把单纯问某实体或若干实体“有多少/多少个/多少量/什么状态”某指标解释成跨实体总和；执行：只有 question/evidence 出现 total、sum、overall、combined、across all、in total、per group、average、mean、most common 等明确聚合信号，或明确要求统计候选实体/记录数量时才保留聚合，否则改为直接输出指标列。多个匹配实体本身不是聚合信号；“people/users/customers who satisfy condition”通常只是候选行过滤，不授权 `SUM(metric)` 或 `GROUP BY`。`most common`、`most frequent`、`commonest` 等频率语义要求按候选值分组并按 `COUNT(*)`/`COUNT(value)` 降序取最高频值；不要对 TEXT/类别列直接使用 `MAX(column)` 或 `MIN(column)` 做字典序极值，即使 evidence 简写为 `MAX(column)`，除非 question/evidence 明确要求字典序最大/最小的值。当 `top/bottom N ... by <name/title/id/code>` 或 `... in alphabetical order` 没有给出数值排序指标时，也不要为了 top/bottom 发明 `SUM/COUNT/AVG`。R34/R35 对已有指标列的判断优先于 R69 的一般三分法。

R36. Evidence 指定 `COUNT(*)`、`COUNT(id)`、`COUNT(DISTINCT id)`、`SUM(CASE...)` 等口径时，按指定口径实现。

R37. 触发词：DISTINCT、COUNT(DISTINCT)、unique、different、deduplicate、duplicate rows、canonical pair、symmetric pair。候选 SQL 使用 `DISTINCT` 或 `COUNT(DISTINCT ...)` 时，禁止为了“更安全”“更像实体列表”“避免重复”而无条件添加；只有 question/evidence 明确要求 unique/distinct/different/不同取值，或 evidence 指定 DISTINCT，或 R33/R77 的目标实体计数例外成立时，才保留 `DISTINCT`。BIRD 中 list/type(s)/kind(s)/category/categories 默认不等于 unique；`how many types/kinds/categories of <records>` 中的 type/kind/category 名词本身也不等于 `COUNT(DISTINCT type)`，除非 question/evidence 明确要求 different types、unique categories 或不同取值数量。Evidence 把自然语言实体映射到 ID 列也不等于 unique；单表事件/关系/记录行中同一实体多次出现，默认表示多条候选记录，不自动合并成唯一实体；只有为表达条件而额外 JOIN 到一对多明细/事件/桥表并重复同一目标实体时，才按 R33/R77 判断是否需要实体去重。若表中一行已经区分 first/second、source/target、from/to、left/right、atom1/atom2 等有方向或有序角色，question/evidence 要输出这些原字段时，禁止添加 `col1 < col2`、`col1 <= col2`、`MIN/MAX(col1,col2)`、规范化 pair key 等自造条件来合并对称行；只有 question 明确要求 undirected/canonical pair、unique pair、deduplicate reciprocal rows 时才做规范化去重。

R38. Question/evidence 明确 unique、distinct、different、各不相同、不同取值，或指定 `COUNT(DISTINCT ...)` 时，使用 `DISTINCT` 或等价分组。仅出现 type/types/kind/kinds/category/categories 等名词时，不自动推出 DISTINCT；这类词可以是普通列名或记录属性，计数时仍按 R33/R37/R77 判断行数、实体数或不同取值数。

R39. 输出目标是实体或取值集合，且 question/evidence 没有要求自然行、前 N 行、记录实例或重复出现次数，JOIN 到翻译、合法性、规则、别名、桥表、明细或历史表只为过滤/解释而引入重复时，可以用 `DISTINCT` 保持目标集合语义。

R40. 触发词：COUNT(DISTINCT target)、DISTINCT subquery、GROUP BY before COUNT、deduplicate before count。计数 SQL 使用 `COUNT(DISTINCT target)`，或先用 `DISTINCT`、`GROUP BY`、`ROW_NUMBER() OVER (PARTITION BY ...) WHERE rn = 1` 等子查询去重后再 `COUNT` 时，先检查 evidence 是否已经给出普通 `COUNT(target)`、`COUNT(target where ...)`、`Divide(COUNT(target ...), COUNT(target ...))` 等公式；若给出且没有 DISTINCT，必须删除 DISTINCT，改为 evidence 的普通 COUNT 口径。只有 evidence 没有指定普通 COUNT 公式时，才继续判断是否满足 R33/R77 的目标实体计数例外。若 question 问满足条件的实体数量，而条件只能通过从目标实体到一对多明细/事件/桥表 JOIN 表达，`COUNT(DISTINCT target_entity_key)` 可以保留；若候选来源表本身就是满足条件的事件/关系/记录行，则普通 have/has/with/whose/matching record 等存在性措辞本身不是 DISTINCT 信号；计数题的存在性口径仍按 R33/R37/R77 判断。

R41. 多行拥有相同展示值不必然是重复错误；如果 BIRD 题目意图是列出行级结果，保留重复行。

R42. `ORDER BY ... LIMIT` 或无排序 `LIMIT N` 定位前 N 条、top/bottom 行或样例行时，默认作用于原始候选行；只有 question/evidence 要求唯一集合、不同实体或不同取值时才先 `DISTINCT`。

R43. 触发：候选 SQL 加入的表没有提供任何 question/evidence 所需的输出字段、过滤字段、枚举解释、精确短语来源或关系路径；禁止：为“看起来完整”额外 JOIN 其他表；执行：删除真正无作用的 JOIN。若 JOIN 表提供了 metadata、disambig 或查询验证指向的更精确字段或枚举值，不能用本规则要求删除。

R44. 需要另一个字段、条件、枚举含义、精确短语来源或关系路径时，只加入服务于这些目的的 JOIN；JOIN 最小化是默认风格规则，不能覆盖 R05、R66、R67、R71 中的精确来源选择。

R45. 触发：多张表有相似字段、同义字段或可组合字段；禁止：只匹配 question 中的一个词就替换成相关但不等价的字段；执行：按 question/evidence 的完整短语、字段来源、列说明和枚举值选择最精确来源，必要时先查询候选枚举值确认。

R46. 触发：候选 SQL 使用 `LEFT JOIN`；禁止：为了覆盖潜在无记录对象、补 0、补 NULL 或保留展示实体而默认左连接；执行：普通存在关系改为 `INNER JOIN`。若被 JOIN 的表提供 question/evidence 要求的输出标签、名称、分类、状态、过滤值、度量或关系证据，未匹配行会产生 NULL 输出或把不具备该关系的对象纳入答案，此时不得用 `LEFT JOIN`。例外：若 question 的目标实体集合和主要过滤条件由主实体表定义，而相关表只提供补充输出属性或度量、且 question/evidence 没有要求该补充记录必须存在，则可用 `LEFT JOIN` 保留主实体集合。只有 question 明确要求保留无匹配对象、零记录对象、缺失值、包含没有记录的对象、反向查缺，或上述主实体集合例外成立时，才保留 `LEFT JOIN`。

R47. 多条角色路径可达同一对象时，先区分 owner、author、participant、winner、host、source、target、teacher、student、buyer、seller 等角色，再选路径。

R48. 多个同构槽位只有在 question/evidence 要求全部槽位时才 `UNION`、`OR` 或展开；否则使用被明确指向的代表槽位。若 schema 把同一角色拆成 primary/secondary/third、first/second/third、slot1/slot2/slot3、name1/name2/name3、contact1/contact2/contact3 等编号或优先级槽位，未限定的自然语言角色默认指 primary/first/slot1 主槽位；不要因为其他槽位也能命中同一人名、组织名或代码就自动扩展到 secondary/additional/third 槽位。只有 question/evidence 明确写 any/all/either/also/secondary/additional/alternate/co-/assistant/deputy/第二/额外/任一等全槽位或扩展槽位语义，或 metadata 明确说明这些槽位没有主次差别且共同构成同一集合时，才展开多个槽位。

R49. JOIN 键优先使用 schema、外键或 hints 指示的原始键。格式修复（补零、CAST、substr、printf、`+0` 数值化等）只有在 question/evidence 明确要求代码格式转换，或原始键连接完全无法命中题目目标时才加入；若原始键连接已经能返回匹配目标行，不要为了提高覆盖率、补齐疑似漏行或迎合 metadata 中的通用修复建议而做 key repair。若题目所需字段可由两张表直接通过原始键连接得到，优先使用直接连接路径，不额外加入桥表或格式修复。

R50. highest、lowest、top、bottom、maximum、minimum、largest、smallest、most、least、earliest、latest 等要求实体或行时，使用 `ORDER BY metric DESC/ASC LIMIT N`。若 question 要“won the most / occurred most / has most”等最高计数组，默认 `GROUP BY target ORDER BY COUNT(*) DESC LIMIT 1`；不要用 `HAVING COUNT(*) = (SELECT MAX(...))` 返回所有并列，除非 question 明确要求 ties/all tied；也不要在没有 tie-break 要求时额外添加 `, name/id/code ASC` 等二级稳定排序。若 question 写 `top/bottom N ... by <name/title/id/code>`、`... in alphabetical order` 或 `... alphabetically`，且没有同时给出 amount、score、count、total、sum、average、time、date、rank 等数值/时间排序指标，则不要发明隐式 `SUM/COUNT/AVG` 排名；默认输出满足过滤条件的目标属性，必要时 `DISTINCT` 去重展示值，并按该属性 `ORDER BY ... ASC LIMIT N`（只有明确写 descending/逆序时才降序）。

R51. Question 要极值本身时，输出 `MAX`、`MIN` 或等价表达；question 要极值对应的实体或属性时，排序取行。

R52. 第 N 高/低使用 `ORDER BY ... LIMIT 1 OFFSET N-1`；并列只有在 question/evidence 要求 ties、all tied、所有并列时才保留全部。

R53. 触发：候选 SQL 使用 `ORDER BY`；禁止：在 question/evidence 没有排序、排名、top/bottom、latest/earliest 或 order-dependent 要求时添加排序；执行：删除不服务于题意的 `ORDER BY`，并同步删除只为排序存在的表达式或 JOIN。若 question 明确要求 alphabetical order、alphabetically 或按 name/title/code/id 展示顺序，排序键应是对应展示属性，而不是为了排序额外构造的隐式聚合指标。

R54. 没有指定 tie-breaker 时，不额外添加稳定排序键。

R55. 触发词：time literal、duration literal、timestamp、date literal、HH:MM:SS、MM:SS.mmm、seconds、minutes、milliseconds、TEXT numeric/date/time。TEXT 存数字、金额、百分比、时长、日期或代码时，比较和排序前用 schema、样例值或查询确认格式，必要时转换类型；转换后的写法必须有数据库样例、非零命中查询、evidence 公式或明确格式说明支持，不能把已验证 0-hit 的字面值改成另一个仍无命中的自造精确值。若 question/evidence 的时长或时间字面量精度与列格式不同（例如题面只有分秒而列含毫秒，或题面用另一种分隔格式），应优先用已查询验证的非零命中格式、前缀匹配、范围比较或数值化表达保留题面精度；只有精度完全一致并有命中时才做精确等值。若题面时间只精确到分钟/秒，而列值含小数秒、毫秒或更细粒度，不要改成某个单一最接近的精确值；应使用前缀匹配、同精度范围比较或数值化后按题面精度过滤。粗粒度时间过滤命中多行时，除非 question/evidence 明确给出 earliest/latest/top/closest/smallest/largest、唯一实体或其它 tie-breaker，不要添加 `ORDER BY ... LIMIT 1` 把多行压成单行；若最终只输出某个属性且多行属性可能重复，可按 R37/R53 判断是否需要 `DISTINCT` 和是否保留排序。DATETIME/TIMESTAMP 列或存储为 `YYYY-MM-DD HH:MM:SS` 的文本时间戳与日历日期字面量比较时，若 question/evidence 是 after/before/on 某一天，应保留 evidence 给出的日期字面量和比较方向，但用 `date(column)` 与 `'YYYY-MM-DD'` 比较；不要直接用原时间戳字符串和日期短字符串比较。候选 SQL 若写成 `timestamp_col > 'YYYY-MM-DD'`、`timestamp_col >= 'YYYY-MM-DD'`、`timestamp_col < 'YYYY-MM-DD'` 等原始时间戳字符串比较，会把边界日当天的部分时间错误纳入或排除，属于格式/粒度错误；应改为 `date(timestamp_col)` 与同一个日期字面量比较。这个转换是日历粒度对齐，不是改写 evidence literal。

R56. when 问发生时间时输出具体日期/时间字段，不要只输出用于排序或定位的年份、赛季、月份序号或排名键。which year/season、what year/season 才输出年份或赛季标识。若 evidence 说明 last/max(year)、latest year、earliest year 等条件，该年份表达式只用于找到目标行；只要 question 的提问词是 when/what date/what time，最终 SELECT 应返回该目标行的 date/time/timestamp 字段。候选 SQL 若只 `SELECT MAX(year)` 或只输出排序年份，但 schema 中存在对应行的 date/time/timestamp 字段，必须改为先定位该行再输出具体时间字段。

R57. date 和 time 拆列且 question 要完整时间时输出两个原字段；只问 clock time 时才只输出 time。

R58. XML、HTML、JSON 等结构文本，问原字段时输出原值，问自然语言内容时才提取内容。

R59. `UNION`、`INTERSECT`、`EXCEPT` 只用于明确集合运算，或多个同构来源必须合并成同一输出 schema。

R60. 最终自查 `SELECT`：只包含 question/evidence 要求的列或表达式，列顺序与题意一致。

R61. 最终自查 `WHERE`：没有隐藏状态、隐藏类型、隐藏类别、活跃记录、当前记录、默认记录、非空、非空串、最新记录、非系统记录或业务清洗过滤。尤其不要因为领域常识、外部常识、数据库名称、任务领域名、样例探索、metadata 中的空值率/类型说明，额外添加 question/evidence 没写明的状态/类型/类别/层级代码、actor 非空、输出文本非空或数据质量过滤；只有 question/evidence 明确把短语映射到该枚举值、类型编号、代码值、非空要求或排除系统记录时才保留。若 question 里的形容词只是数据库领域、任务域或对象类别的一部分（如 `<domain> database`、`<domain> record`、`<domain> entity` 等），且 schema/evidence 没有提供对应分类列或枚举值，不要用外部常识把它改写成某个具体值过滤。若 question 使用领域形容词修饰要输出的对象或属性，但数据库只提供对象本身的候选值、没有提供可验证的分类列/枚举/证据映射，则输出数据库中满足结构条件的候选对象，不要凭常识缩小到少数值。若某个动作/历史/事件表已经作为目标关系或文本来源被选中，不要再把同一个宽泛动作词二次消费为额外事件子类型过滤；自然语言动作词（edited/created/updated/deleted/voted/commented/ordered/processed 等）本身只足以帮助选择动作/历史/事件来源，不足以自动授权具体 subtype/code 枚举过滤，也不足以自动添加 actor/user/person 非空过滤或非系统过滤。`users/persons who <action>` 这类短语默认描述动作来源关系，不等于要求排除 actor 为空的历史行；只有 question/evidence 明确要求该子类型、明确给出 subtype/code 映射、明确要求 non-system/by registered users/known user，或不加该 subtype/code 会使已选来源明显包含另一类同名目标对象时，才保留事件子类型或 actor 非空过滤。

R62. 最终自查公式：分子、分母、标度、聚合层级和比较方向与 evidence/question 一致。

R63. 最终自查聚合：`COUNT`、`SUM`、`AVG`、`GROUP BY`、`DISTINCT` 与目标行粒度一致，没有把已有指标列误当成明细行聚合。计数题中 `DISTINCT` 是否允许由 R33/R37/R40/R77 优先决定；不要用笼统的“更合理”“更安全”“避免重复”覆盖 evidence 普通 COUNT 公式。若 question 的目标确实是满足条件的实体数量，且从目标实体到明细/事件/桥表的 JOIN 会重复同一目标实体，R33/R77 可以要求用 `COUNT(DISTINCT target_entity_key)` 修正计数粒度；若最终候选来源本身就是事件、授予、交易、评论、投票、历史、合法性、打印或其他记录行，或 question/evidence 明确要求行、记录、事件、交易、历史记录，或 evidence 指定普通 COUNT，则 R63 不能要求 DISTINCT。

R64. 最终自查排序：`ORDER BY`、`LIMIT`、`OFFSET` 只服务于 question/evidence 指定的 top/bottom/rank/latest/earliest 目标。

R65. 最终自查 JOIN：每个 JOIN 都为必要输出字段、过滤条件、枚举解释、精确短语来源或关系路径服务，没有无依据地扩大或缩小候选集；不要仅因为另一个表存在近似字段就替换已由 metadata、disambig 或查询验证支持的 JOIN。

R66. 触发：question/evidence 出现由限定词、方向词、来源词、状态词、对象词和值词组成的复合业务短语；禁止：只实现其中一个词、丢掉修饰词，或用多个语义相近但不等价的字段组合近似表达；执行：先逐词核对候选 SQL 是否覆盖完整短语的每个语义成分，再寻找能整体匹配完整短语的列名、列说明、disambig 或枚举值，若存在则优先使用该单一精确来源，若不存在才拆分为多个必要条件。不要把普通属性短语过度解释成 all/every/each/always 等遍指条件，除非 question/evidence 明确出现这些词或等价表达。R66 不能覆盖 R48：当完整短语只指一个未限定角色，而 schema 提供 primary/secondary/third 等编号槽位时，完整覆盖该短语通常是使用主槽位，不是自动 OR 所有槽位。

R67. 触发：question 的过滤短语可能对应多个表或列，或候选 SQL 只实现了过滤短语的一部分；禁止：在未检查完整列名、列说明、disambig 和枚举值前选择更粗的替代列，也禁止把复合短语拆成一个更宽泛的布尔标志或近似字段后停止探索；执行：优先使用与完整过滤短语更精确匹配的来源表和字段，必要时 JOIN 到该来源表完成过滤，只有不存在精确来源时才考虑语义相近的替代列。R67 优先于 R43/R44 的少 JOIN 默认。final reviewer 不应用 R67 因“另一列也可能更精确”而拦截；只有候选 SQL 明显没有实现 question/evidence 写出的短语成分时才触发。

R68. 如果一个表同时含实体级行和上级汇总行，例如 school 与 district、player 与 team、city 与 country，question 指向实体时应选择实体级行；question 指向上级汇总时才选择汇总行。

R69. 触发：候选 SQL 在 `COUNT(*)`、`SUM(metric)`、`MAX(metric)` 和 `SELECT metric` 之间做了选择；禁止：因为 question 写 how many 就把三者互相替代，也禁止把“最高月份/月度总体/annual monthly maximum”等月级目标误写成单条客户月记录的 `MAX(metric)`；执行：问行数/实体数用 `COUNT`，问明确跨行总量用 `SUM(metric)`，问匹配实体或记录上的已有指标值用 `SELECT metric`。当事实表一行是“某客户某月”的月度记录，question 要某年最高 monthly consumption/monthly amount/monthly metric 时，先按月份 `GROUP BY` 并 `SUM(metric)` 得到每月总体，再 `ORDER BY SUM(metric) DESC LIMIT 1` 或外层取最大月总量。多个匹配实体本身不是 `SUM(metric)` 的充分理由；必须有 total、sum、overall、combined、across all、in total、monthly total/highest monthly 等总量或月级聚合信号。若 question 同时要求 `total/sum/SUM(metric)` 和列出对应实体/事件/类别名称（如 `list the name of the event they were spent on`），聚合粒度应保持在被列出的实体/事件/类别上：`GROUP BY` 该输出实体/事件/类别并输出每组 `SUM(metric)`；不要用不分组的标量子查询或窗口表达式把全局总和重复到每个实体行。

R70. 触发：question 要的是已有事实表或度量表中的指标值；禁止：在目标行集本身由该指标表记录定义时，为了覆盖没有匹配指标记录的实体而改用 `LEFT JOIN`、补 0、补 NULL 或额外过滤非输出展示字段；执行：候选集默认来自拥有该指标的匹配记录。例外：若 question 的目标实体集合和主要过滤条件来自主实体表，指标表只是补充输出属性或度量，且 question/evidence 没有要求补充指标记录必须存在，则按 R46 的主实体集合例外比较并保留 `LEFT JOIN` 候选。

R71. 触发：同一概念既可由单一精确字段/枚举值表达，也可由多个近似字段组合表达；禁止：在存在精确字段/枚举值时使用近似字段组合，或为了减少 JOIN 而把精确来源替换成近似组合；执行：优先使用单一精确字段/枚举值，组合近似字段只在没有精确来源时使用。

R72. 触发：question 用单数定指实体（如 the customer、the player、the record、the transaction）定位对象并要求该对象的其他属性，但查询验证显示多个不同实体满足同一定位条件；禁止：在没有排序、限制或其他定位条件时假装只有一个对象；执行：若 question/evidence 要输出该实体身份，可加入本地主键、短 ID、code 或 number 辅助消歧；若 question/evidence 只要求该对象的若干属性，最终 SELECT 不应额外加入未要求的消歧列，应优先保持题目要求的输出列并按 R52/R54 处理并列和排序。

R73. 触发：计数、比例或条件聚合题使用 `WHERE outer_id IN (SELECT inner_id FROM detail/bridge/event ...)`、`EXISTS (...)` 或先分组再计数来表达另一个明细表、桥表、事件表、翻译表、合法性表、徽章表、评论表、交易表、月度事实表中的条件；禁止：在 evidence 明确给出普通 `COUNT(id)`、`SUM(CASE...)`、百分比分母或明细扩行口径时，用半连接把多条明细/关系/事件行压成一个外层实体后再计算，从而改变 evidence 公式。若 question/evidence 要统计候选行、记录、事件、交易或历史记录，默认使用 `INNER JOIN` 到提供条件的明细/关系表，并按 JOIN 后的最终候选行粒度执行。若 question 要统计满足条件的外层实体数量，且条件来自一对多明细/关系表，可以使用 `COUNT(DISTINCT outer_entity_key)` 或等价半连接统计外层实体；若被查询的表本身已经是该条件的事件/关系记录来源，则不要先压成唯一外层实体。本规则必须与 R33/R77 的目标实体计数例外一致，不能覆盖 evidence 普通 COUNT 公式。

R74. 触发：evidence 明确把数据库标签或代码值映射为自然语言含义，例如 `label = '+' mean ...`、`label = '-' means ...`，question 要判断对象是否属于该分类；禁止：把数据库标签擅自翻译成 `yes/no`、`true/false`、`carcinogenic/non-carcinogenic` 等自然语言文本或自造文本，也不要把没有该标签的结构行计入已分类对象总体；执行：最终输出原始标签/代码列或 evidence 指定的表达式，保持 BIRD gold 风格的数据库字面值。Question 中的 tell whether、whether it is、if they are、is it 等自然语言分类问法，只说明要返回该分类字段，不等于要求把代码翻译成自然语言文本。仅当 question 明确要求输出 yes/no、true/false、natural-language status、words/text label、write "carcinogenic"/"non-carcinogenic" 等自然语言词面，或 evidence 明确给出要输出的词形时，才用 `CASE` 翻译标签。若同一题同时要求结构条件和标签分类统计，分类总体只包含有标签的对象；`among these` 后续分类计数必须遵守 evidence 的条件聚合口径，若 evidence/gold 风格要求在 JOIN 后行集上 `SUM(CASE WHEN label/code THEN 1 ELSE 0 END)`，不要改写成另一个唯一实体计数。保留无标签结构对象只在 question 明确要求 all structure records / orphan / unlabeled 时使用。

R75. 触发：question 在同一句中要求多个标量结果，例如 `how many ... and ... how many ...`、`what is X and Y`、`count ... and among these count ...`；禁止：用 `UNION`/`UNION ALL` 把多个标量答案纵向堆成多行，除非 question 明确要求列表、分组或多行集合；执行：把这些标量作为同一个 `SELECT` 的多个输出表达式，按 question 顺序横向输出为多列。

R76. 触发：evidence 使用 `refers to`、`refers to column`、`refers to <field>`、`= <column>` 或等价说明，把一个完整自然语言短语直接映射为输出列、指标列、公式列、枚举列或代码列；执行原则：该完整短语已经被 evidence 用来选择列或表达式，不能再被二次消费为独立的行选择条件。禁止：候选 SQL 又根据同一个短语添加隐藏过滤、首次/原始/非再版/最新/最早/当前/default 行选择、版本标志过滤、维表 JOIN、`ORDER BY`、`LIMIT 1` 或其他收缩行集的条件。若映射短语本身含 original/originally/as originally printed/first/latest/current/default 等词，这些词默认属于列含义的一部分，不是独立的时间线或版本行选择要求；R76 优先于 R50/R52/R53/R64 等排序/极值规则。执行：保留 evidence 映射列和 question 明确写出的实体/类别/时间/数值过滤；删除只由同一短语二次推导出的行选择条件。若同一短语同时出现在 question 和 evidence 映射中，question 中的重复出现不算独立行选择条件；只有 question/evidence 除该列映射短语之外另有不同短语明确要求 earliest/latest/current/default、首次/原始版本、存在性或特定行版本，才保留对应条件。输出列的 `IS NOT NULL` 只按 R20 的输出值有效性例外单独判断，不由本规则禁止或要求。

R77. 触发：候选 SQL 出现 `COUNT(DISTINCT id)`、`COUNT(DISTINCT user_id)`、`COUNT(DISTINCT customer_id)`、`COUNT(DISTINCT player_id)` 或其他 `COUNT(DISTINCT <entity key>)`。判定顺序：第一，若 evidence 明确写普通 `COUNT(column)`、`COUNT(column where ...)`、`Divide(COUNT(column ...), COUNT(column ...))` 或类似公式，且没有 DISTINCT，则必须使用普通 COUNT，不要保留或新增 DISTINCT；这一步优先于目标实体、percentage of entities、避免 JOIN 重复、去重更合理等所有理由。第二，若 question/evidence 明确写 unique、distinct、different、不同、唯一或指定 `COUNT(DISTINCT ...)`，保留 DISTINCT。第三，只有当 question/evidence 的目标确实是满足条件的外层实体数量，且 SQL 为表达条件必须从该外层实体表 JOIN 到一对多明细、事件、桥表或历史表从而重复同一外层实体时，才保留 `COUNT(DISTINCT target_entity_key)`。若不能同时证明“目标是外层实体数量”和“JOIN 会把同一目标实体扩成多行”，不要因为 how many、users/customers/items 等复数实体词、have/with/whose 等存在性措辞、evidence 的 `refers to <id column>` 映射、metadata/hints 说明同一实体可出现多次、或查询发现 `COUNT(*)` 与 `COUNT(DISTINCT key)` 不同而使用 DISTINCT。第四，若 question/evidence 明确要求 row/record/entry/event/history/transaction/matching record，或候选来源表本身就是满足条件的事件/关系/记录行，则按最终候选行粒度 `COUNT(*)` 或 `COUNT(key)`，删除 DISTINCT。本规则禁止预防性去重，但不禁止 R33 定义的目标实体计数。

R78. 触发：evidence 把过滤短语直接写成列级谓词或比较条件，例如 `<phrase> refers to <column> >= value`、`<phrase> = <column> < value`、`<phrase> means <column> = code`。执行：按该列级谓词放入 `WHERE` 或 evidence 指定的条件位置；不要把同一个谓词改写为 `SUM(column)`、`AVG(column)`、`MAX(column)`、`MIN(column)`、`GROUP BY ... HAVING`、存在性子查询或跨行累计条件。若 evidence 中的谓词直接出现列名 token（例如 `<measure_col> > 50`、`<count_col> >= 4`、`<lab_col> < 300`），优先匹配 schema 中同名或大小写/引用符差异后的精确列名；不要在未比较精确列名前选择拼写相近、单复数不同、含义相邻但不是同一列的字段。若同一精确列名在多表中都存在，再用 question、join path、row grain 和已读 metadata 消歧。若 question 中出现 total/amount 等自然语言修饰，但 evidence 已明确给出列级谓词（如 `<phrase> refers to <count_col> >= 4`），该 evidence 谓词优先，不能把它升级成 `SUM(<count_col>) >= 4`。只有 evidence 本身给出 `SUM/AVG/MAX/MIN/GROUP BY/HAVING` 公式，或 question/evidence 在该谓词之外另有明确 across rows/per group/total sum/average/max/min 要求时，才把列级谓词提升为聚合条件。

R79. 触发：计数 SQL 在单表或主来源事件、关系、明细、历史、交易、检测、授予、投票、评论等记录表上使用 `COUNT(DISTINCT foreign_entity_id)`。执行：若 question/evidence 没有明确写 unique/distinct/different/不同/唯一，且没有从外层目标实体表 JOIN 到一对多来源造成目标实体重复，则必须删除 DISTINCT，改为候选行计数 `COUNT(*)`、`COUNT(row_id)` 或 evidence 指定的普通 `COUNT(column)`。`<entity> refers to <id column>` 只是列映射或连接键说明，不等于要求唯一实体计数；metadata/hints 说明同一外键实体可出现多次，或查询显示 `COUNT(*)` 与 `COUNT(DISTINCT foreign_entity_id)` 不同，也只说明两种口径不同，不等于授权 DISTINCT。只有 R36/R38 的显式 DISTINCT 或 R33/R77 的外层实体一对多扩行例外可以保留 DISTINCT。

R80. 触发：question 用 connected/linked/paired/bound/related/edge/relationship 等关系词询问两个同类或异类实体之间的连接，且 schema 中存在明确的关系表、边表、连接表或成对端点列（如 source/target、from/to、left/right、entity_a/entity_b、entity1/entity2）。执行：输出关系记录上的两个端点列，保留关系行粒度；不要把结果压成单列 `DISTINCT endpoint`，也不要只输出其中一个端点，除非 question 明确要求列出参与过该关系的唯一实体集合、某一侧端点或去重实体列表。若 question/evidence 要求关系方向或角色，按原端点列顺序输出，不自造对称去重。
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
