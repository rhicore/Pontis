"""Agent Join/Rel Detection — Agent 分析列关系，创建 .rel 实体

所有关系检测（join、overlap、semantic rel）都由此 agent 完成。
基于 overlap 统计数据和列实际内容，由 AI 判断是否创建关系实体。
"""
import logging

from storage import Store

logger = logging.getLogger(__name__)

MAX_ROUNDS_DEFAULT = 60

PROMPT = """\
你的任务：分析项目中数据库列之间的关系，找出有意义的关联并创建 .rel 实体。

## 什么是"有意义的关系"

两个列之间存在关系，可能是：
- **外键关系**：一列引用另一列（如 orders.user_id → users.id）
- **同名同义**：不同表中含义相同的列（如两个表都有 dept_name）
- **语义关联**：通过数据内容可以推断的关联（如 zip_code 和 city）

## 两种发现途径

### 途径一：基于统计线索（overlap / fk）
项目中可能有 .overlap 和 .fk 实体，这些是统计层面的线索。
**注意**：overlap 是静态近似计算，可能遗漏真正可以 join 的列对。不能只依赖这些线索。

### 途径二：自主判断（更重要）
除了统计线索，你必须主动分析表结构和列内容来发现关系：
- 看列名：语义相近的列（如 user_id / uid / customer_id）可能是关联
- 看数据：用 lookup 或 read 查看列的实际值，判断两列是否有值重叠
- 看表结构：理解表之间的业务逻辑关系（如订单表必然关联用户表）

**两条途径都要走**。即使没有 overlap 线索，只要你有充分理由认为两列有关联，就应该创建 .rel。

## 工作流程

### 1. 发现项目中的数据库
用 glob '*.db', '*.sqlite', '*.sqlite3', '*.duckdb' 找到所有数据库。

### 2. 对每个数据库建立全局认知
a. glob 查看所有表
b. meta 查看每张表的基本信息（行数、列数）
c. 查看已有的 .overlap、.fk、.rel 实体
d. 理解每张表的业务含义（通过表名、列名、数据内容）

### 3. 检查统计线索
对每个有 .overlap 或 .fk 的列对：
- 用 meta 查看统计数据
- 用 lookup 或 read 查看**两端列的实际数据**验证语义
- 确认后创建 .rel

### 4. 自主发现额外关系
主动扫描尚未被 overlap 覆盖的列对：
- 重点关注：名称含 id/code/no 等关键词的列（潜在外键）
- 对不同表中语义相近的列名，用 lookup 抽样验证值是否有重叠
- 对业务上必然关联的表（如订单-用户、课程-教师），检查对应的关联列

### 5. 创建 .rel 实体
对于确认有意义的关联，用 create_entity 创建 .rel 实体：
- ref 格式: `[数据库]::[表1].[列1]__rel__[表2].[列2].rel`
- meta 中包含:
  - from_table, from_column, to_table, to_column
  - rel_type: 关系类型（fk / same_meaning / semantic）
  - source: 发现途径（"overlap" / "fk" / "self_discovered"）
  - reason: 判断依据（你看到了什么证据）
  - brief（≤50字）和 detail（完整描述关系）

### 6. 注意
- 不要只依赖统计重叠，必须验证语义（看实际数据）
- 自主发现的判断必须有数据支撑（至少看过列名和抽样值），不要凭空猜测
- 两列即使 overlap 很高，如果语义不同（如两个表都有 "name" 列但指不同实体），不要创建
- 用中文写 brief 和 detail
- 已有的 .rel 不要重复创建
"""


def generate(store: Store, *, max_rounds: int = MAX_ROUNDS_DEFAULT,
             debug: bool = False) -> None:
    """分析所有 DB 文件的列关系，创建 .rel 实体。"""
    from agent.agent import PontisAgent
    from agent.tools import build_writer_registry, enable_debug
    from agent.prompt import build_prompt
    from agent.config import load_agent_config

    config = load_agent_config(store.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping agent join detect")
        return

    logger.info("=== Agent Rel Detection ===")

    registry = build_writer_registry()
    if debug:
        enable_debug(registry)

    agent = PontisAgent(
        store.project_path,
        tools=registry,
        system_prompt=build_prompt("writer", store.project_path, debug=debug),
    )

    agent.chat(PROMPT, max_rounds=max_rounds)
    logger.info("=== Agent Rel Detection done ===")
