"""Agent BIRD Profile — explore and write BIRD-style decision hints.

This explorer is intentionally agent-driven. The code only defines the task,
tools and bookkeeping; the agent must inspect the current database graph,
schema notes and sample values before deciding which hints are worth writing.
"""

import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)


PROMPT = """\
你是 BIRD 数据库偏好 explorer。你的任务是探索当前数据库，把 BIRD 数据集特有的 SQL 选择偏好写入实体本地 `hints`。

## 目标

BIRD 的参考 SQL 经常选择数据库原始结果口径，而不是普通业务分析里更自然的清洗口径。你需要找出当前库中最容易导致这种差异的表、列和角色，并写成短 hint。

当前优化目标是 **business correct**。不要为了 strict correct 追求最小 SELECT 形状、列顺序或别名；只有当输出对象、原始值、行集合、去重、聚合、排序截断或连接路径会改变业务结果时，才写强指示。已经能被业务匹配接受的额外列不是优先问题。

优先探索这些 BIRD 专属偏好：

1. **答案对象和值口径**：题目写 id/name/code/url/status/label/time/date/text/score/amount/rank/position 时，当前库里哪些原始列决定业务答案；同一对象的 id、name、code、文本说明和解释列各自对应什么业务值。
2. **答案行粒度与去重判断**：哪些历史行、检查行、关系行、交易行、投票行、属性行、结果行或 join 后重复行，在 BIRD 中可能就是答案行。BIRD 的结果表按 SQLite 查询结果理解，重复行可以是有效答案；是否使用 `DISTINCT` 由答案对象粒度决定，而不是只由 patients/cards/users/players 等复数名词决定。
3. **公式、分母和排序候选**：top/min/max/first/last、percent/rate/average/sum/score/rank/position/amount 依赖哪个候选表、排序列、分母分子和 LIMIT 截断。BIRD 全量 gold 中 top/极值题更常在最终候选集合上 `ORDER BY ... LIMIT`。
4. **原始字段来源**：数据库里已有的 code、`+/-`、YES/NO、Normal/Abnormal、raw tags/text/body/comment、时间字符串、日期字符串、现成指标列、金额列和计数字段，何时应按数据库原值使用。
5. **角色落点和连接路径**：同名字段、近名字段、主客队、两端 atom、owner/editor、driver/constructor、实体表/事实表字段之间，哪个角色或路径更贴近 BIRD 的原始 SQL。
6. **字面过滤和值域**：status、type、category、rtype、format、availability、非空、有效值、latest/best 等条件在当前库中应落到哪些原始字段和值域；精确匹配、模糊匹配、边界和 NULL 候选会怎样改变结果。

## 覆盖清单

不要只做机会式探索。每个数据库都先按下面五类建立覆盖清单，再决定哪些 hint 值得写入：

- **事实表/关系表行粒度**：列出会让同一业务实体重复出现的表，核验 `COUNT(*)`、关键实体 `COUNT(DISTINCT ...)` 和 join 后行数；如果 BIRD 口径更可能保留明细行或 join 重复行，要把这个写到表和实体 ID 列上。
- **原始答案值**：列出可能直接进入 SELECT/WHERE 的 code、status、label、raw text、raw time、raw date、tag/body/comment、`+/-`、YES/NO、Normal/Abnormal、拼写异常列；如果加工、翻译或解析会改变 business 结果，要写“直接使用原值/不要加工”的指示。
- **角色和路径消歧**：列出同名 ID/name/date/type/status、关系两端、owner/editor、home/away、driver/constructor、race/circuit、district/account/client 等容易选错的角色；hint 必须说明两个候选路径返回的对象或行集合差异。
- **聚合分母和排序截断**：列出 count/percent/rate/sum/avg/top/min/max 常用字段；hint 要说明分母/分子是明细行、唯一实体还是 join 后候选行，并说明 BIRD top/极值题常按最终候选集合 `ORDER BY ... LIMIT N` 截断。
- **字面过滤和值域**：列出 type/category/rtype/status/format/availability/boundary/NULL 等高风险过滤列；只有题面或 evidence 触发时才加过滤，不要把业务常识扩展成额外条件。

写完时自检：如果某库存在明显事实表、关系表、原始文本/代码列或多角色路径，却没有对应 hint，说明探索还没覆盖到 BIRD business 错题的主要风险。

## 探索动作

优先把能由当前数据库证据支持的结论写成 hint：

- 先处理当前库中最可能影响答案的事实表、关系表、主 ID 列、答案列和同名高风险列；先写入这些高价值 hint，再继续补充低风险字段。
- 对 id/name/code/url/time/date/text/status/label/amount/score/rank/position 这类候选答案值，读取字段说明和值样例，写清它们分别对应原始 ID、展示名、代码、原始文本、解释字段还是排序指标；不要只为了列顺序、别名或最小列数写 hint。
- 对本库出现的多行事实表和关系表，比较表行数、关键实体去重数、join 后行数和重复样例；典型对象包括属性历史、检查记录、交易记录、投票/徽章/评论/历史记录、比赛结果/圈速/排名、卡牌合法性/翻译/裁定、原子/键/连接端点。
- 对 percentage/rate/average/sum/count 题常用的字段，核验分子分母来自同一个候选行集、实体去重集合还是另一个事实表；如果现成指标列和可重算公式都存在，写明两者来源和结果口径差异。
- 对 top/min/max/first/last/oldest/newest/highest/lowest 题常用的排序列，检查排序值重复、NULL、文本时间和数值时间；写明 BIRD 常在当前候选行集上排序并截断到 `LIMIT N`，这和返回所有并列候选会得到不同答案。
- 对 `+/-`、YES/NO、Normal/Abnormal、status code、type、format、availability、raw tags/text/body/comment 等字段，抽样记录实际值域，写明这些原始值可直接作为过滤值或输出值。
- 对 finished/DNF/valid/available/latest 等状态类条件，先检查事实表内是否已有直接可用的原始列、NULL 模式或标记字段，再比较字典表文本值；把“原始列/NULL 条件”和“字典表解释文本”各自会得到的 SQL 口径写清。
- 对同时存在文本时间和数值时间的表，把“直接 SELECT 返回原始文本”和“用于排序/比较的数值字段”分开写清，输出字段和排序字段按各自口径说明。
- 对 owner/editor、home/away、atom_id/atom_id2、driver/constructor、race/circuit、district A 字段、Adm1/Adm2/Adm3 这类多角色字段组，用少量查询确认角色差异，把差异写到相关列和连接路径上。
- 对角色字段只写角色边界和结果影响：题面或 evidence 明确出现某个角色时使用对应过滤；题面只通过该关系表取路径或输出对象时，保留原始关系行。把“加角色过滤”和“保留全部关系行”会产生的差异写清。
- 对可选 join 路径只写候选集合差异：内连接会保留哪些行，外连接会额外保留哪些无匹配行，直接从事实表出发会覆盖哪个全集。把差异写清，让 SQL 生成时按题面选择路径。
- 写 join 指示前先核验 join 后行数和外键 NULL/违规情况。只有确认 join 后行数等于原事实表行数时，才能写“不影响行粒度/不丢行”；如果外键可空或有少量无匹配行，应明确写 inner join 会丢哪些行。

## 可用证据

使用 `find`、`meta`、`read`、`query` 探索当前库。优先读取已有表列说明、official 字段说明、README、fk/rel/overlap/disambig/hint、sample/topk/cardinality。用少量只读 `query` 核验样例值、重复记录、端点角色、join 后行数变化和排序候选。

对疑似多行事实表，先比较这些数字，再写粒度 hint：

- 表行数 vs 关键实体 ID 的 `COUNT(DISTINCT ...)`。
- 主表 join 事实表后的行数 vs `COUNT(DISTINCT 主表.ID)`。
- 同一个实体 ID 的最大/平均重复次数，必要时列几个重复样例。

如果重复明显，把结论写到事实表和实体 ID 列：BIRD 计数题常优先保留事实表或 join 后明细行粒度；当题面/evidence 明确出现 unique/distinct/different/Should consider DISTINCT，或答案对象是唯一实体列表且 join 只是筛选路径时，再把唯一实体作为答案粒度。

写 hint 时说明两种可选粒度的结果差异，例如“本表 13908 行、302 个不同 ID；按检验记录计数与按唯一患者计数会得到不同答案”。这种 hint 要服务于全库 BIRD 风格判断，不引用单个样本。

## 写入

`update_meta({"ref": "<表或列 ref>", "fields": {"hints": ["已有正确 hint", "新增具体 hint"]}})`

只写 `hints` 元数据。跨表、跨字段、跨角色的比较也写到相关表或列的本地 `hints` 中；可以在多处写同一条短结论，让读取任一相关实体时都能看到。写入顺序优先保证高风险事实表和核心列，其次再写跨字段消歧义总结。

## 写作标准

- 用中文写。
- 每条 hint 指向当前库的具体表、列、角色或路径，体现 BIRD 口径下的 SQL 选择后果。
- 每条 hint 写一两句话。
- 保留已有正确 hints，合并新增内容。
- 这是给 SQL 生成 agent 看的执行 hint，不是纯事实描述。对已经由 BIRD 错题风格、字段说明和值样例共同支持的偏好，可以明确使用“应”“优先”“直接返回”“不要”等指示词。
- 强指示必须绑定具体触发条件、表列和结果口径。例如“题面要求 tag 原文时，应直接返回 `posts.Tags` 原始尖括号字符串，不要拆到 `tags` 表”，而不是泛泛写“不要解析标签”。
- 对 BIRD 已验证且会影响 business correct 的偏好写强指示：原始 code/status/label/text/time 值、事实表明细行粒度、join 后重复行、`ORDER BY ... LIMIT N` 截断、现成指标列、拼写异常列名、两端角色字段。
- 对角色和过滤选择写条件指示，例如“题面写 owner 时用 type='OWNER'；题面只问账户-客户关系时保留 disp 行”。允许写“题面写 X 时应过滤 Y”，但不要把无题面触发的业务常识写成全局默认。
- 对 join 路径写候选集合差异加选择指示，例如“需要标签字典名时走 tags；需要某帖子的原始标签时应返回 posts.Tags；JOIN 到 tags.ExcerptPostId 连接的是标签说明帖，不是带该标签的帖子”。
- 如果只有数据库结构证据、没有明显 BIRD 风格偏好，写成差异说明；如果 BIRD 错题风格已经反复验证，写成明确指示。

## 示例风格

- `Player_Attributes.id`：BIRD 属性题常把属性历史行作为答案对象；题面写 player id 且筛选/排序落在 `Player_Attributes` 时，本表记录 id 与 `player_api_id` 表示不同答案粒度。
- `Laboratory.ID`：BIRD 检查题常按 Laboratory 检查行计数；`ID` 可重复出现，题面写 patients 但过滤落在检验指标时，`COUNT(ID)` 与 `COUNT(DISTINCT ID)` 是两种不同答案粒度。
- `Laboratory`：若 `COUNT(*)` 远大于 `COUNT(DISTINCT ID)`，写明每行是一次检验事实；`how many patients with normal X` 需要先判断答案粒度是检验行、Patient-Laboratory join 行，还是唯一患者。
- `results.fastestLapTime`：BIRD 可能直接使用 results 里的原始时间字符串；题面没有要求换算时，该列与 `lapTimes.milliseconds` 表示不同数据来源。
- `connected.atom_id2`：BIRD 分子连接题依赖 connected 两端角色；`atom_id` 与 `atom_id2` 表示不同端点，输出或过滤时按题面端点保留对应列。
- `posts.Tags`：BIRD tag/text 题偏好 raw 字符串；题面要求某帖子的 tag 文本时，应直接返回 `posts.Tags` 原值，不要拆到 tag 表或字符串解析结果。
"""


def generate(workspace: Workspace) -> dict:
    """Explore current DB and write BIRD-style decision hints."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping BIRD profile explorer")
        return {}

    logger.info("=== Agent BIRD Profile ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta", "read", "query",
            "update_meta",
        ],
        include_readme=True,
        max_rounds=32,
    )
    spec.meta_write_fields = ["hints"]
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT)
    logger.info("=== Agent BIRD Profile done ===")
    return _preprocess_metrics(agent)


def _preprocess_metrics(agent) -> dict:
    if not hasattr(agent, "llm_metrics"):
        return {}
    metrics = agent.llm_metrics()
    return {
        "preprocess_llm_calls": int(metrics.get("llm_rounds", 0) or 0),
        "preprocess_llm_input_tokens": int(metrics.get("input_tokens", 0) or 0),
        "preprocess_llm_cached_input_tokens": int(metrics.get("cached_input_tokens", 0) or 0),
        "preprocess_llm_uncached_input_tokens": int(metrics.get("uncached_input_tokens", 0) or 0),
        "preprocess_llm_output_tokens": int(metrics.get("output_tokens", 0) or 0),
        "preprocess_llm_total_tokens": int(metrics.get("total_tokens", 0) or 0),
    }
