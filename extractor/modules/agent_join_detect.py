"""Agent Join Detect — 发现高置信度的列间关联，创建 .rel 实体。

唯一职责：基于数据库全局结构，发现真正高概率的列关联关系。
不负责写总结（由 agent_analyze 处理）或消歧（由 agent_disambiguate 处理）。

核心原则：
- 宁缺毋滥：只创建高置信度的关系，宁可遗漏也不要误报
- 全局一致性：每个列在同一角色下只应关联一个目标列
- 明确标注不确定性：所有 .rel 都是 AI 推断，不是确定事实

独立执行:
    python -m extractor.agent_join_detect ./my_data
"""
import logging

from storage import Store

logger = logging.getLogger(__name__)

PROMPT = """\
你的任务是分析数据库列之间的关系，只创建**高置信度**的 .rel 实体。

## 核心原则：宁缺毋滥

你创建的每一个 .rel 都会被下游 agent 作为 JOIN 线索参考。错误的 .rel 比缺失的 .rel \
危害更大——agent 会因为错误的关联线索而写出错误的 SQL。

因此：
- **只创建你非常确定的关系**，不确定的不创建
- **考虑数据库全局结构**，不是每个有值重叠的列对都需要创建 .rel
- 已经有 .fk 覆盖的关系不需要再创建 .rel（除非 .rel 提供了 .fk 没有的额外信息）

## 你的唯一目标

发现数据库列之间的高置信度关联，创建 .rel 实体。
但如果你在分析过程中发现已有实体的 brief/detail 有明显错误，可以修正。

## 什么是值得创建的 .rel

**应该创建**：
- 两列之间有明确的业务关联（如 orders.customer_id ↔ customers.id）
- 两列的值高度重叠且语义相同（Jaccard > 0.3 或 coverage > 0.5）
- 业务逻辑上这两张表必然需要 JOIN 的场景

**不应该创建**：
- 两列只是"有点像"或"可能有关系"——不确定就不创建
- 两列语义相近但用途不同（如 `Free Meal Count` vs `FRPM Count`，前者是子集）
- 已经有 .fk 覆盖的同一关系（方向相反的 .fk + .rel 算同一关系）
- 值域重叠只是巧合（如两个表都有 "Los Angeles" 但不构成关联）

## 全局一致性约束

在创建 .rel 前，你必须从数据库全局视角思考：

### 1. 唯一 JOIN 伙伴原则
如果列 A 是表 T1 的主键/关联键，它通常只应关联一个目标列。\
如果你已经发现 A ↔ B 是高置信度关系，那 A ↔ C 就需要更强的证据才能创建。

举例：`schools.CDSCode` 已经通过 .fk 关联 `frpm.CDSCode` 和 `satscores.cds`，\
那么 `schools.CDSCode` 不应再关联其他列。

### 2. 排他性检查
如果你打算创建 A ↔ B 的 .rel，先检查：
- A 是否已有 .fk 指向其他列？如果有，B 是否只是同一关联路径上的不同表达？
- B 是否已有 .fk 或 .rel 指向其他列？A 是否与现有关系矛盾？
- 是否存在传递路径 A → C → B 使得 A ↔ B 的直接关联是冗余的？

### 3. 传递冗余
如果已有 A ↔ C 和 C ↔ B，通常不需要再创建 A ↔ B（除非 A ↔ B 有独立的业务含义）。

## 工作流程

### 1. 建立全局视图
- glob 找到数据库和所有表
- meta 读取每张表的信息
- **读取所有 .fk 实体**，建立确定的外键关系图
- 读取所有 .overlap 实体，作为候选线索

### 2. 逐一评估 overlap 线索
对每个 .overlap 候选：
- meta 查看统计（Jaccard、coverage、cardinality）
- 检查是否已有 .fk 覆盖 → 已覆盖则跳过
- 检查两端列的实际数据（read 或 lookup）
- 评估全局一致性：这两列是否应该关联？是否有矛盾？
- 只有高置信度才创建 .rel

### 3. 自主发现（谨慎进行）
除了 overlap 线索，你也可以主动发现关系，但标准更高：
- 必须有充分的数据证据（读过列名、统计数据、抽样值）
- 必须通过全局一致性检查
- 不要"发现"太多关系——大多数关系已经通过 .fk 和 overlap 覆盖了

## 创建 .rel 实体的规范

### 命名
- ref: `[数据库]::[表1].[列1]__to__[表2].[列2].rel`
- 创建前先 glob 检查是否已存在（含反向）

### meta 内容
brief 和 detail 必须遵循以下规范：

**brief 格式**：
"[高/中]置信度：简要描述关系"
示例：
- "高置信度：satscores.cds 通过 CDSCode 关联 schools，需注意前导零差异"
- "中置信度：schools.District 与 frpm.District Name 语义相同但格式可能有差异"

**detail 内容必须包含**：
1. **推断依据**：基于什么证据判断（Jaccard 值、抽样验证、业务逻辑）
2. **使用注意事项**：JOIN 时需要注意什么（格式差异、前导零、空值等）
3. **不确定性声明**：明确说明这是 AI 推断的关系
4. **置信度理由**：为什么给这个置信度

**detail 示例**：
"AI 推断关系（非物理外键）。基于 overlap 检测（Jaccard=0.39）和抽样验证确认两端值格式一致（14位 CDSCode）。\
schools 以 CDSCode 为主键，frpm 通过 CDSCode 外键关联 schools（已有 .fk 实体），本 .rel 是 .fk 的反向视角。\
JOIN 时无需特殊处理。置信度：高（有物理外键佐证）。"

### edges
必须连接列到 rel（不是列到列），需要两条边：
- {"a": "[db]::[table1].[col1].[TYPE].col", "b": "[db]::[rel_entity]"}
- {"a": "[db]::[table2].[col2].[TYPE].col", "b": "[db]::[rel_entity]"}

## 语气规范

**必须做到**：
- 用"推断"、"可能"、"建议"等谨慎措辞
- 明确标注"AI 推断关系（非物理外键）"
- 区分置信度（高/中），低置信度直接不创建

**禁止做到**：
- 不要用"完全匹配"、"可直接 JOIN"、"同一概念"等断言式表述
- 不要暗示 .rel 和 .fk 有同等可靠性
- 不要省略不确定性声明

## 注意

- 用中文写 brief 和 detail
- 不要为已有实体写 summary（除非发现明显错误需修正）
- 已有的 .rel 不要重复创建
- 如果数据库只有 .fk 关系且不需要额外 .rel，可以不做任何操作
"""


def generate(store: Store, *, debug: bool = False) -> None:
    """发现高置信度列关联，创建 .rel 实体。"""
    from agent.agent import create_agent, AgentSpec
    from agent.utils import load_agent_config

    config = load_agent_config(store.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping agent join detect")
        return

    logger.info("=== Agent Join Detect (high-confidence only) ===")

    agent = create_agent(store.project_path, AgentSpec(
        mode="writer",
        debug=debug,
    ))

    agent.chat(PROMPT)
    logger.info("=== Agent Join Detect done ===")
