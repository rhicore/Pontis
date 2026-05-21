"""Agent Disambiguate — 发现语义歧义，创建 disambig 实体。

唯一职责：扫描同名/近名实体，判断语义差异，创建 disambig 实体并更新相关列的 detail。
不负责发现关系（rel）或写总结（由 analyze 处理）。

独立执行:
    python -m explorer.disambiguate ./my_data
"""
import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT = """\
你的任务是深入分析项目中数据库的语义歧义问题，发现并记录名称相同或相近但含义不同的实体。

## 你的目标

发现数据库中同名或近名实体的语义差异，创建 disambig 消歧实体。

## 什么是语义歧义

以下情况会产生歧义，需要消歧：
- **同名列不同语义**：多个表有同名的列，但含义不同
- **近名列不同语义**：列名相似但不完全相同，容易混淆
- **同名/近名表不同用途**：名称相近的表可能服务于不同场景
- **同义不同名**：指向同一概念但列名不同

## 工作流程

### 1. 发现项目中的数据库
优先用 `find({"ref":"*:file:db"})` 找数据库；如果项目里没有数据库文件，再考虑其他来源。

### 2. 对每个数据库建立全局认知
a. find 查看所有表
b. meta 查看每张表的基本信息
c. 查看所有列实体，收集列名到表名的映射
d. 查看已有的 fk、overlap、rel、disambig 实体

### 实体引用规范
- 表使用路径 ref：`financial.sqlite/account`
- 列使用路径 ref：`financial.sqlite/account/account_id`
- 不要使用 `table.column` 做 `meta`、`update_meta`、`add_edge`
- 如果某个 overlap / rel / fk 名字里出现 `table.column`，那只是名称，不是可直接传给工具的 ref

### 3. 扫描列级歧义
收集所有列名，找出出现在多个表中的同名列：
- 用 find 获取所有列
- 按列名分组，找出出现在 >= 2 个表中的列名
- 用列路径 ref 查看实际数据和 meta，判断语义是否真的不同
- 如果同名列在不同表中含义完全相同，不需要消歧
- 除非列的已有 sample/topk/detail 明显不够，否则不要额外对辅助文件或非数据库源做 query

### 4. 扫描表级歧义
查看所有表，找出名称相近或用途重叠的表。

### 5. 创建 disambig 实体

ref: `[你概括的共同模式]:disambig`

meta:
- brief: ≤50字描述歧义核心
- detail: 客观列出每个涉及的实体的具体语义差异

edges: 连接到所有涉及的实体（不限制类型和数量）

### 6. 更新相关列的 detail

为涉及歧义的列实体更新 detail，追加事实性消歧信息。

更新列时，`ref` 必须使用列路径 ref，例如 `financial.sqlite/card/type`。

## 注意

- 不是所有同名列都需要消歧
- 判断歧义必须基于实际数据
- 用中文写 brief 和 detail
- 只描述客观差异，不要给操作建议
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
