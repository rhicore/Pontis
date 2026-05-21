"""Agent Query Overview — 从同库全部 query/evidence 抽取查询先验。

该 explorer 面向 batch / transductive 场景：给定当前数据库对应的一组
question/evidence，抽取对后续单题 SQL 生成有直接帮助的语义口径、
值映射、消歧规则和一致性约束，并重写 README 中的 query-derived 章节或相关实体元数据。

该模块通常在 agent_readme 之后运行。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """\
你的任务是基于当前数据库的 schema 图谱和同库所有待回答问题，抽取对后续 SQL 生成有直接帮助的查询先验，并增强 README 和相关实体元数据。

核心产物是一组可执行的 SQL 决策规则。每条规则都必须说明它在 SQL 中扮演的角色，避免后续 query agent 把背景解释误用成过滤条件。

README 的 query-derived 章节必须短、密、靠前高价值。后续 query agent 会在系统提示词中直接读取 README；如果规则过长，关键信息会被稀释。

## 输入

下面给出的是当前数据库对应的全部 question/evidence。

{cases_block}

## 目标

你需要利用 question/evidence 暴露出来的真实查询需求，检查当前数据库图谱是否足以支持后续 agent 正确回答这些问题。

重点不是概括题集，而是抽取能直接减少 SQL 错误的先验知识：
- 自然语言术语对应哪个表/列/枚举值/计算公式
- evidence 中给出的口径定义应如何落到 schema 和 SQL
- 同一个概念在不同 query 中是否必须保持同一解释
- 哪些短语存在竞争候选，如何用 schema、value、样本查询或 evidence 消歧
- 哪些信息只是解释输出/公式/字段含义，不能自动变成 WHERE 过滤条件
- BIRD exact-match 容易受影响的输出列、列顺序、聚合、排序、rank 写法
- 当前 README、列 meta、knowledge、disambig 是否缺少这些关键约束

最终产物应当帮助后续单题 agent 更少猜错表、列、值、join、聚合口径和过滤条件。

## 权限

你拥有正常 writer agent 的能力：
- 读取数据库、表、列、fk、rel、overlap、disambig、README、说明文件等 meta
- 执行少量 SQL 验证候选路径和值分布
- 创建辅助 knowledge / disambig / rel 实体
- 修正明显错误或缺失的实体 brief/detail
- 给相关实体补充客观的查询语义、值映射、计算口径和消歧规则
- 更新 README 的 brief/detail，把旧的 query-derived prior knowledge 替换为新的 SQL 决策规则

## 产出规则

- 只从 question/evidence、schema、数据库说明、样本值和查询验证中得出结论
- README 是给后续 query agent 使用的操作性知识，不是题集摘要
- 只写会改变 SQL 生成行为的内容：表列选择、值选择、join、公式、粒度、时间/排名/聚合口径、输出形状
- 对每条先验写出来自 question/evidence 的证据片段、schema grounding、SQL 使用方式和必要验证
- 如果 question/evidence/schema 之间存在潜在矛盾，要写清一致性约束或剩余不确定项
- README 内容应聚焦可执行的 SQL 先验，而不是任务意图分布、代表问题清单或输出类型统计
- 每条规则必须有 `SQL role`，取值使用以下之一：
  - `select_column`: 决定 SELECT 输出字段
  - `filter_column`: 决定 WHERE 使用的字段和值
  - `formula`: 决定计算表达式
  - `join_grain`: 决定主表、join path、去重键或聚合粒度
  - `ordering_limit`: 决定 ORDER BY、LIMIT、rank/window
  - `output_shape`: 决定列顺序、是否单列/多列、是否聚合
  - `disambiguation`: 给出候选字段/值和判别证据
  - `background`: 只解释概念、单位、口径或字段含义；只有题目/evidence 直接要求时才转成 SQL 条件

## 决策覆盖清单

生成 README 前必须检查下列高风险 SQL 决策是否已经被明确覆盖。若当前数据库没有对应概念，可省略；若存在竞争候选，写成 disambiguation 规则。

### README 排序与长度

- `## Query-Derived Prior Knowledge` 章节控制在约 80-120 行，优先保留最可能改变 SQL 正误的规则。
- 章节前 20 行必须放最关键的字段来源和输出形状规则；长枚举表、题号清单、背景解释放后面或写入辅助实体。
- 每条规则用 1-4 行表达，避免大段叙述。长表格优先写到 knowledge/disambig 实体，在 README 中只留查询时需要的选择规则。

### 字段来源

- **同名/近义字段在多个表出现时**，分别写出 SELECT、WHERE、ORDER BY 的优先来源。例如学校名、县/市/学区、特许资助类型、年级上下限、学校类型、教育运营类型、教育阶段。
- **funded / funding / charter-funded** 这类短语要检查是否存在普通学校字段和 FRPM/charter funding 字段两个候选；写清何时用哪个列。
- **district code / county code / school code / ownership code** 这类短语要定位到真实代码列，不要只写自然语言列。
- **type of education / school type / institution level / educational operation / district control** 这类短语要列出竞争体系，并给出题面词触发的候选优先级。
- **lowest/highest grade / grade span / grades X-Y / K-12** 要区分分离列与字符串年级跨度列；公式口径说明不能自动变成过滤条件。

必须优先写清这些常见歧义：

- `direct charter-funded` / `directly charter-funded` / 同时出现 `charter` 与 `funded`：优先 `frpm."Charter Funding Type" = 'Directly funded'`；若 evidence 指定 `frpm."Charter School (Y/N)"`，同一题中也使用 frpm 的 funding 列。
- 输出字段是 `funding type` 且 SQL 已使用 frpm/SAT 路径时，优先输出 `frpm."Charter Funding Type"`；`schools.FundingType` 是普通学校表字段，覆盖更广但不等价。
- `district code`：优先 `frpm."District Code"`；`schools.DOC` 是 district control code，`schools.NCESDist` 是 NCES 学区 ID，二者不是 BIRD 问题里的 district code。
- `lowest grade` / `highest grade`：优先 `frpm."Low Grade"` / `frpm."High Grade"`；只有题目明确说 grade span offered/served 时才解析 `schools.GSoffered` / `GSserved`。
- `type of education offered`：优先 `schools.EdOpsName`；`EILName` 是 institution level，`SOCType` 是 school ownership/type，`DOCType` 是 district control。
- `high schools` 作为 FRPM 条件时，检查 `frpm."School Type" = 'High Schools (Public)'`；`EILCode='HS'` 是学校表教育阶段候选，不等价。

### 输出形状

- 根据题面输出字段顺序写规则：`what is A ... indicate B` 通常输出 A 再输出 B；`list X along with Y` 输出 X, Y；`name ... and ...` 按题面顺序输出。
- `how many <items>` 通常是 `COUNT`；`how many test takers` 若问的是人数/参考人数列本身，应返回 `NumTstTakr`，只有出现 total/sum/overall 才聚合求和。
- `active and closed ...` 表示同一个集合的合并过滤时，输出一个总 count；只有题面要求 “for each status/by status” 时才 GROUP BY。
- `rank` 要输出 rank/window 列；`7th/333rd highest` 用 ORDER BY + LIMIT/OFFSET，不额外添加 tie-breaker，除非题面要求。
- 默认不添加 `DISTINCT`、`ROUND`、`IS NOT NULL`、额外排序或额外过滤；只有题面/evidence/SQL 执行必要性要求时使用。
- `what is A and B ... indicate C` 输出顺序是 A, B, C；不要把 “indicate C” 提前到第一列。
- `which county ... provide school and closure date` 必须输出 county 以及题目随后要求的 school/date。
- `compared to all other types` 的 ratio 分母是 `count(non-target)`，不是 `count(all)`；若短语包含 `charter school funding`，过滤 `Charter = 1`。

### SAT 粒度

- `satscores.rtype` 是候选决策而非全局固定过滤。学校级输出通常用 `rtype='S'`；但当题面要求 district、district-level count，或 school-level 条件无命中而 district-level 有命中时，要把 `rtype='D'` 作为竞争候选写入规则。
- 当 SAT 表中的 `cname/dname/sname` 可直接回答地理或学区问题时，写清是否需要 join 到 `schools`，以及 join 后会不会改变粒度。

## README 增强内容

在 README detail 中写入一个章节，标题为 `## Query-Derived Prior Knowledge`。如果旧 README 已有同名章节，先整体替换该章节；该章节应使用紧凑的规则格式；没有实际信息的部分可以省略。

每条规则推荐写成：

```markdown
- Term: ...
  SQL role: select_column | filter_column | formula | join_grain | ordering_limit | output_shape | disambiguation | background
  Use when: ...
  Grounding: table.column / value / formula / join path
  SQL pattern: ...
  Competing candidates: ...
  Verification: ...
```

README 中优先覆盖这些类别：

1. `### 字段与公式口径`
   - 自然语言术语 -> 表/列/值/公式
   - 公式是否返回 0~1 ratio、0~100 percentage，还是原始 count
   - evidence 中的解释如果只是公式口径，应标成 `formula` 或 `background`，而不是 `filter_column`
2. `### 值、枚举与地理词消歧`
   - query/evidence 中的文本值 -> 数据库真实取值
   - 城市/县/学区/邮寄城市等竞争字段需要写候选和判别证据
   - 大小写、缩写、代码值、布尔/三值枚举、日期格式、单位
3. `### 结构、粒度与 Join`
   - 学校级/学区级、学校信息表/SAT表/FRPM表的职责边界
   - join path、主表选择、去重键、聚合分母
   - 某字段出现在多个表时，写清 SELECT/WHERE/ORDER BY 各自应优先从哪里取
4. `### 输出形状与 Exact-Match 约束`
   - 题目要求的字段顺序、是否返回 count/sum/list/rank/window
   - “rank/list/count/which/how many”等措辞对应的输出形状
   - 只有题目要求分组结果时才使用 GROUP BY；问总数时返回单个聚合值
5. `### 仍需单题验证的歧义`
   - 无法仅凭 question/evidence/schema 判定的点
   - 单题 agent 应执行的最小验证 SQL 或 meta 查询

## 写入方式

优先更新 README。读取现有 detail 后，生成完整的新 detail 并一次性替换：

```text
meta({"ref": "README", "property": ["brief", "detail"]})
update_meta({"ref": "README", "fields": {"brief": "...", "detail": "..."}})
```

如果 README 不存在，先创建 README，再写入 brief/detail。

你也可以创建或更新辅助 knowledge / disambig / rel 实体，并把它们连到相关 db/table/col/fk，
但不要为了 query overview 强制创建一个单独的 overview 节点。

完成后用：

```text
meta({"ref": "README", "property": ["brief", "detail"]})
```

确认写入成功。完成后直接停止。
"""


def _case_id(case: dict[str, Any], fallback: int) -> Any:
    return case.get("question_id", case.get("id", fallback))


def format_cases(cases: Iterable[dict[str, Any]], *, max_chars: int | None = None) -> str:
    """Format query/evidence cases for the agent prompt.

    ``max_chars`` is kept for API compatibility. Query overview is explicitly
    a transductive pass over all question/evidence pairs, so it does not
    truncate the case list.
    """
    lines: list[str] = []
    for idx, case in enumerate(cases):
        qid = _case_id(case, idx)
        question = str(case.get("question") or "").strip()
        evidence = str(case.get("evidence") or "").strip()
        if not question and not evidence:
            continue
        block_lines = [f"### Q{qid}", f"Question: {question}"]
        if evidence:
            block_lines.append(f"Evidence: {evidence}")
        lines.append("\n".join(block_lines))
    return "\n\n".join(lines) if lines else "(没有提供 question/evidence)"


def load_cases(path: str | Path, db_id: str | None = None) -> list[dict[str, Any]]:
    """Load query/evidence cases from a BIRD-style JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"case file must be a JSON list: {path}")
    if db_id is None:
        return [case for case in data if isinstance(case, dict)]
    return [
        case for case in data
        if isinstance(case, dict) and str(case.get("db_id", "")) == db_id
    ]


def generate(
    workspace: Workspace,
    cases: Iterable[dict[str, Any]] | None = None,
    *,
    max_case_chars: int | None = None,
) -> None:
    """Enhance project README and metadata with query-derived prior knowledge."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping query overview")
        return

    cases_block = format_cases(cases or [], max_chars=max_case_chars)

    logger.info("=== Agent Query Overview ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta", "query",
            "create_entity", "update_meta", "add_edge", "delete",
        ],
        include_readme=True,
    )
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT_TEMPLATE.replace("{cases_block}", cases_block))
    logger.info("=== Agent Query Overview done ===")
