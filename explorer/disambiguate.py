"""Agent Disambiguate — 发现语义歧义，创建 disambig 实体。

唯一职责：扫描同名/近名实体和同值域/值域重叠实体，判断语义差异，增量维护 disambig
实体。
关系发现由 rel explorer 处理，总结由 analyze 处理。

独立执行:
    python -m explorer.disambiguate ./my_data
"""
import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT = """\
你的任务是分析项目中数据库实体之间的语义歧义，并把可复用的消歧知识写入图谱。

核心立场：数据库里的两个实体只要是不同实体，就不能写成等价、同义或可替换。它们必然在来源、粒度、覆盖范围、编码、值域、行过滤、输出角色或连接后果上存在差异。`disambig` 不是同义词集合，而是记录候选实体为什么不能互相替代。

同一个脚本同时支持：
- 没有相关 disambig 时创建新实体并连边。
- 已有相关 disambig 时读取、修正、补边、去重；如果存在重复或误导 disambig，用 `delete` 删除。
- 已有 disambig 如果把实体写成“含义相同/等价/可替代/差不多/均表示同一概念”，必须改写为差异说明；如果无法找到差异证据，先保守写“当前证据不足以证明可替代”，不能写等价。

## 候选范围

重点处理这些候选竞争：
- 同名列在不同表中代表不同业务对象、不同粒度或不同用途。
- 近名列容易被同一个自然语言词触发，但实际含义不同。
- 同值域/值域重叠（value-domain overlap）的列：即使列名不近，只要 sample、topk、枚举代码、ID 格式、地名/机构名集合或 `overlap` 实体显示它们落在同一值域或有明显交集，也必须作为消歧候选。
- 列值同域不等于语义等价：两个列都出现 `Los Angeles`、`Active`、`Y/N`、`K-12`、学校代码、县名、学区名、学校名、状态枚举或类型代码时，必须写清它们各自的来源、粒度、行集和 SQL 角色。
- 一个自然语言词可能落到多个表/列，例如 type、category、status、language、date、amount、text、name、id。
- 代码列和名称列即使一一对应，也不是同一输出角色：代码常用于过滤/连接/枚举，名称常用于展示；题面要求 code/name/type 时必须区分。
- 同一业务主题的 count/rate/percent、free/FRPM、offered/served、physical/mailing、school/district/county 等字段必须按口径分别描述。

写作边界：
- 消歧实体的核心价值是辨析差异；两个数据库实体即使名称、业务主题或值域接近，也按不同实体描述。
- 写入内容只陈述数据库事实，采用事实边界、格式、粒度、覆盖范围和值域差异表述。
- 读取列时优先查看 `official_column_description` 和 `official_value_description`；它们是人工/官方标注，优先于 AI/agent 生成的 `brief/detail`。消歧结论不得和 official 字段冲突。
- 如果存在一一对应关系，也必须写清 SQL 后果，例如“代码列用于过滤枚举值，名称列用于输出描述文本；二者不可按题面要求互换”。
- 不要只写“用于不同场景”。必须说明自然语言触发词与具体实体的边界，例如 `NCES school identification number -> NCESSchool`，`NCES district identification number -> NCESDist`。

## 工作流程

1. 用 `find({"ref":"*:file:db"})` 找数据库。
2. 用 `find` 和 `meta` 建立全局 schema 认知：表、列、fk、overlap、rel、已有 disambig。
3. 必须读取 `find({"ref":"*:overlap"})`，把列值重叠关系作为消歧候选来源。`overlap` 不代表可以 JOIN；它代表两个列可能共享值域，恰好需要判断是否会让下游 agent 混用。
4. 按四类线索分组候选实体：
   - 名称线索：同名、近名、缩写/全称、code/name/type/status/count/rate/percent。
   - 自然语言线索：多个列会被同一个问题词触发。
   - 值域线索：sample/topk/枚举/格式/ID 长度/地名/机构名集合相同或重叠。
   - 图谱线索：fk、rel、overlap、已有 disambig 连接到相同表或相同业务主题。
5. 对候选实体读取 `meta`，必要时用 `query` 查看少量实际值，确认它们的语义差异。对同值域候选，必须比较 sample/topk/cardinality/null_percentage 或 overlap stats。
6. 检查是否已有同类 `disambig`：没有则创建；已有且正确则更新 detail 或补边；已有但重复、错误、过强或包含软等价措辞则改写或删除。
7. 对本轮读取过或维护过的已有 disambig 做措辞审查；发现等价/可替代/均表示类表述时，即使实体内容大体正确，也要改写成差异表述。
8. 复查本轮维护的 `disambig`，确保每个消歧实体都通过 edges 连接到所有涉及实体。

## 引用规范

- 表使用路径 ref，例如 `financial.sqlite/account`。
- 列使用路径 ref，例如 `financial.sqlite/account/account_id`。
- Related 中的邻接实体使用 `主节点ref/邻接名称:分组标签`。
- overlap、rel、fk 名称里出现的 `table.column` 是实体名称，不是工具 ref；工具调用使用路径 ref 或 Related 组合 ref。

## 写入格式

创建实体：
`create_entity({"ref": "共同模式:disambig", "meta": {"brief": "...", "detail": "..."}, "edges": [...]})`

`brief` 用一句话说明歧义核心，必须突出“不可替代的差异”，不要把候选实体写成同义集合。推荐句式：
- `A、B、C 不是同义字段：A ...，B ...，C ...`
- `A 与 B 属于不同口径/编码/粒度，不能互换`
- `自然语言 X 指向 A；自然语言 Y 指向 B`

`detail` 使用稳定结构：
- 候选实体：列出每个实体 ref。
- 各自语义：说明每个实体分别代表什么；不要用一个总称覆盖所有候选。
- 差异维度：来源、粒度、覆盖范围、编码体系、值域、单位、空值、行过滤、输出/过滤/排序/连接角色。
- 语境边界：说明自然语言线索与各实体之间的事实边界；能落到具体字段时写成明确映射。
- SQL 后果：说明选错实体会导致的具体 SQL 错误，例如错列、错口径、错 JOIN、错粒度、错输出列、错过滤条件。
- 值域证据：如果候选来自同值域/值域重叠，写明 overlap stats、共同样例、topk、枚举、格式或 ID 规则；同时明确“值域重叠不是可替换证据”。
- 验证证据：列出来自 official 字段、meta、sample、topk、query、overlap、fk 或 rel 的依据。

## 禁止项

- 禁止把候选写成同义集合；每个候选实体至少有一条与其他候选不同的事实边界。
- 禁止正向软等价词：`含义相同`、`语义相同`、`同一概念`、`等价`、`可替代`、`差不多`、`都表示`、`均表示`、`均描述`。
- 允许明确否定替换的表述：`不可互换`、`不能替代`、`不可无条件 JOIN`、`不是同一口径`、`不是同一编码体系`。
- 如果写不出差异证据，不要创建“相似实体集合”式 disambig；先保守写“当前证据不足以证明可替代”，不能写等价。
- 用中文写 brief 和 detail。
"""


def generate(workspace: Workspace) -> None:
    """发现语义歧义，创建 disambig 实体。"""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping agent disambiguate")
        return

    logger.info("=== Agent Disambiguate ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta", "query",
            "create_entity", "update_meta", "add_edge", "delete",
        ],
        include_readme=True,
    )
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT)
    logger.info("=== Agent Disambiguate done ===")
