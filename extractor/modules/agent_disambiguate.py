"""Agent Disambiguate — 发现语义歧义，创建 disambig 实体。

唯一职责：扫描同名/近名实体，判断语义差异，创建消歧实体并更新相关列的 detail。
不负责发现关系（rel）或写总结（由 agent_analyze 处理）。

独立执行:
    python -m extractor.agent_disambiguate ./my_data
"""
import logging
import os

from storage import Store

logger = logging.getLogger(__name__)

PROMPT = """\
你的任务是深入分析项目中数据库的语义歧义问题，发现并记录名称相同或相近但含义不同的实体。

## 你的目标

发现数据库中同名或近名实体的语义差异，创建 disambig 消歧实体。

## 什么是语义歧义

以下情况会产生歧义，需要消歧：
- **同名列不同语义**：多个表有同名的列，但含义不同。如 `points` 在 results 表是单场比赛积分，\
  在 driverStandings 表是赛季累计积分
- **近名列不同语义**：列名相似但不完全相同，容易混淆。如 `position` 和 `positionText`
- **同名/近名表不同用途**：名称相近的表可能服务于不同场景
- **同义不同名**：指向同一概念但列名不同。如 `School` / `School Name` / `sname`

## 工作流程

### 1. 发现项目中的数据库
用 glob '*.db', '*.sqlite', '*.sqlite3', '*.duckdb' 找到所有数据库。

### 2. 对每个数据库建立全局认知
a. glob 查看所有表
b. meta 查看每张表的基本信息
c. 查看所有列实体，收集列名到表名的映射
d. 查看已有的 fk、overlap、rel、disambig 实体

### 3. 扫描列级歧义
收集所有列名，找出出现在多个表中的同名列：
- 用 glob `**/*.col` 获取所有列
- 按列名分组，找出出现在 >= 2 个表中的列名
- 对每个同名列，meta 查看它在不同表中的统计数据
- read 或 lookup 查看实际数据，判断语义是否真的不同
- **重要**：如果同名列在不同表中含义完全相同（都是外键指向同一目标），不需要消歧

### 4. 扫描表级歧义
查看所有表，找出名称相近或用途重叠的表：
- 如 `results` vs `constructorResults`（都含 "results" 但含义不同）
- 如 `schools` vs `frpm`（都涉及学校但数据粒度不同）

### 5. 创建 disambig 实体

ref: `[你概括的共同模式]:disambig`

命名由你自己决定，用英文简短概括这些实体之间的**共同模式或歧义主题**。不只是列名本身，而是能体现歧义本质的概括。
可以涉及 2 个或更多实体（列或表），不限于同名列，只要它们之间存在容易混淆的语义歧义就需要消歧。

命名示例：
- `points` 列在 results 和 driverStandings 中含义不同 → `points:disambig`
- `position` 和 `positionText` 容易混淆 → `position:disambig`
- SOC、SOCType、EILCode 都涉及学校类型分类 → `school_type:disambig`
- results 和 constructorResults 名称相近 → `results_table:disambig`
- schools.School、frpm.School Name、satscores.sname 指向同一概念 → `school_name:disambig`
- rtype='S'/'D' 和 cds 的前导零问题涉及 satscores 的编码体系 → `cds_encoding:disambig`

meta:
- brief: ≤50字描述歧义核心
- detail: 客观列出每个涉及的实体的具体语义差异

edges: 连接到所有涉及的实体（不限制类型和数量）

创建前先 glob 检查是否已存在同名 disambig 实体（`glob "*:disambig"`）。

### 6. detail 写作原则：只描述事实，不给操作建议

消歧实体的 detail **只负责客观描述差异**，不该告诉 agent 具体该怎么做。

**必须包含**：
- 每个实体中该术语的具体语义（是什么、代表什么）
- 数据层面的客观差异（值域、格式、粒度、覆盖范围等）

**禁止包含**：
- 操作建议（如"建议使用 CAST"、"应该加 rtype='S' 过滤"、"JOIN 时注意…"）
- 使用偏好（如"优先用 X 列"、"不要用 Y 列"）
- 场景判定（如"当问题要求 X 时，使用 Y 列"）

具体该用哪个列、该不该加过滤条件，由下游 agent 根据问题自行判断。你的建议如果和 golden SQL 不一致，反而会误导 agent。

**正确示例**：
```
points 在不同表中的含义：
1) results.points：单场比赛得分，每条记录对应一场比赛的积分
2) driverStandings.points：赛季累计积分，每条记录是该车手截至某站的赛季总分
两者数值量级和含义完全不同。
```

**错误示例**（不要这样写）：
```
【使用指引】
- 当问赛季总积分时用 driverStandings.points ← 不要写这种建议
- 当问单场得分时用 results.points ← 不要写这种建议
- ⚠️ 常见错误：… ← 不要写操作建议
```

### 7. 更新相关列的 detail

为涉及歧义的列实体更新 detail，追加事实性消歧信息。格式：
> 区别于 [另一表].[同名列]：[该列的含义]，而本列是 [本列的含义]

只补充事实差异，不加操作建议。

## 注意

- 不是所有同名列都需要消歧——如果含义完全相同，不需要创建 disambig
- 判断歧义必须基于实际数据（看过统计信息和抽样值），不要凭空猜测
- 用中文写 brief 和 detail
- **只描述客观差异，不要给任何操作建议**
"""


def generate(store: Store) -> None:
    """发现语义歧义，创建 disambig 实体。"""
    from agent.config import create_agent, AgentSpec
    from agent.guardrail import build_guardrails
    from agent.utils import load_agent_config

    config = load_agent_config(store.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping agent disambiguate")
        return

    logger.info("=== Agent Disambiguate ===")

    spec = AgentSpec(mode="writer")
    project_name = os.path.basename(os.path.abspath(store.project_path))
    spec.projects = [project_name]
    spec.guardrails = build_guardrails(spec, ["round_limit"])
    agent = create_agent(store.project_path, spec)

    agent.chat(PROMPT)
    logger.info("=== Agent Disambiguate done ===")
