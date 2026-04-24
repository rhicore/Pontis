"""Agent Disambiguate — 语义消歧为主，总结和关系发现为辅。

主目标：发现同名/近名实体的语义差异，创建 .disambig 实体
次目标：发现关系创建 .rel、为相关实体写 summary
"""
import logging

from storage import Store

logger = logging.getLogger(__name__)

PROMPT = """\
你的任务是深入分析项目中数据库的语义歧义问题，发现并记录名称相同或相近但含义不同的实体。

## 你的目标

**主要目标**：发现数据库中同名或近名实体的语义差异，创建 .disambig 消歧实体，\
并为涉及的实体写消歧 summary。\
这是你最核心的任务，请投入最多的精力确保消歧信息的准确性和实用性。

**同时关注以下任务**，在分析消歧的过程中一并完成：
- 发现列之间的关系，创建 .rel 实体
- 为你分析过的实体写 brief/detail summary

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
d. 查看已有的 .fk、.overlap、.rel 实体

### 3. 扫描列级歧义
收集所有列名，找出出现在多个表中的同名列：
- 用 glob `**/*.col` 获取所有列
- 按列名分组，找出出现在 >= 2 个表中的列名
- 对每个同名列，查看它在不同表中的实际数据（用 lookup/read）
- 判断语义是否真的不同（有时同名列含义相同，不需要消歧）

### 4. 扫描表级歧义
查看所有表，找出名称相近的表：
- 如 `results` vs `constructorResults`（都含 "results" 但含义不同）
- 如 `schools` vs `frpm`（都涉及学校但粒度不同）

### 5. 创建 .disambig 实体
对确认有歧义的实体，用 create_entity 创建 .disambig：

**列级消歧**：
- ref: `[数据库]::[column_name].disambig`
- meta:
  - level: column
  - brief: ≤50字描述歧义
  - detail: 完整列出每个表中该列的具体语义，用"区别于"句式
- edges: 连接到每个涉及的列实体
  {"a": "[db]::[table].[col].TYPE.col", "b": "[db]::[column_name].disambig"}

**表级消歧**：
- ref: `[数据库]::[term].disambig`
- meta:
  - level: table
  - brief: ≤50字描述歧义
  - detail: 完整列出每个表的具体用途差异
- edges: 连接到每个涉及的表实体
  {"a": "[db]::[table].table", "b": "[db]::[term].disambig"}

创建前先 glob 检查是否已存在同名 .disambig 实体。

### 6. 更新相关实体
为歧义列的 .col 实体更新 detail，追加消歧信息，帮助使用者区分语义。

## 注意
- 不是所有同名列都需要消歧——如果含义完全相同，不需要创建 .disambig
- 判断歧义必须基于实际数据（看过列名、抽样值、业务含义），不要凭空猜测
- 用中文写 brief 和 detail
"""


def generate(store: Store, *, debug: bool = False) -> None:
    """语义消歧为主，总结和关系发现为辅。"""
    from agent.agent import create_agent, AgentSpec
    from agent.utils import load_agent_config

    config = load_agent_config(store.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping agent disambiguate")
        return

    logger.info("=== Agent Disambiguate ===")

    agent = create_agent(store.project_path, AgentSpec(
        mode="writer",
        debug=debug,
    ))

    agent.chat(PROMPT)
    logger.info("=== Agent Disambiguate done ===")
